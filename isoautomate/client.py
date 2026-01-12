import os
import time
import json
import uuid
import redis
import base64
import datetime
import random
import logging
from dotenv import load_dotenv
import __main__

from .config import (
    DEFAULT_REDIS_HOST, DEFAULT_REDIS_PORT, DEFAULT_REDIS_PASSWORD, DEFAULT_REDIS_DB,
    REDIS_PREFIX, WORKERS_SET, SCREENSHOT_FOLDER, ASSERTION_FOLDER
)
from .exceptions import BrowserError
from .utils import redis_retry

# Set up a logger for the SDK
logger = logging.getLogger("isoautomate")
logger.addHandler(logging.NullHandler())

# --- Robust .env Loading ---
def _load_package_env(custom_path=None):
    if custom_path and os.path.exists(custom_path):
        load_dotenv(dotenv_path=custom_path, override=True)
        return
    cwd_env = os.path.join(os.getcwd(), '.env')
    if os.path.exists(cwd_env): load_dotenv(dotenv_path=cwd_env)
    try:
        if hasattr(__main__, "__file__"):
            main_script_dir = os.path.dirname(os.path.abspath(__main__.__file__))
            main_env = os.path.join(main_script_dir, '.env')
            if main_env != cwd_env and os.path.exists(main_env):
                load_dotenv(dotenv_path=main_env)
    except Exception: pass

_load_package_env()

class BrowserClient:
    """
    Python SDK for isoAutomate.
    Controls remote browsers via Redis queues.
    """

    def __init__(self, redis_url=None, redis_host=None, redis_port=None, redis_password=None, redis_db=None, redis_ssl=False, env_file=None):
        if env_file: _load_package_env(custom_path=env_file)
        
        env_url = os.getenv("REDIS_URL")
        env_host = os.getenv("REDIS_HOST")
        env_port = os.getenv("REDIS_PORT")
        env_pass = os.getenv("REDIS_PASSWORD")
        env_db = os.getenv("REDIS_DB")
        env_ssl = os.getenv("REDIS_SSL", "False").lower() in ("true", "1", "yes")

        self.redis_url = redis_url or env_url
        self.host = redis_host or env_host
        self.port = redis_port or env_port
        self.password = redis_password or env_pass
        self.db = redis_db if redis_db is not None else (env_db or 0)
        self.ssl = redis_ssl or env_ssl
        
        if not self.redis_url and not self.host:
            raise BrowserError("Missing Redis Configuration.")

        try:
            if self.redis_url:
                self.r = redis.Redis.from_url(self.redis_url, decode_responses=True)
            else:
                actual_port = int(self.port) if self.port else 6379
                self.r = redis.Redis(
                    host=self.host, port=actual_port, password=self.password,
                    db=int(self.db), decode_responses=True, ssl=self.ssl, ssl_cert_reqs=None
                )
        except Exception as e:
            raise BrowserError(f"Failed to initialize Redis connection: {e}")

        self.session = None
        self.video_url = None
        self.record_url = None
        self.session_data = {}
        self._init_sent = False

    # --- Redis Wrappers ---
    @redis_retry()
    def _r_rpush(self, key, *values): return self.r.rpush(key, *values)

    @redis_retry()
    def _r_get(self, key): return self.r.get(key)

    @redis_retry()
    def _r_delete(self, key): return self.r.delete(key)

    # --- Context Manager ---
    def __enter__(self):
        self.video_url = None
        self.record_url = None
        self.session_data = {}
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            try:
                logger.info(f"[SDK] Auto-releasing session {self.session.get('browser_id', '')[:6]}...")
                self.release()
            except Exception as e:
                logger.error(f"[SDK] Release failed during cleanup: {e}")
        return False
    
    # --- Connection & Lifecycle ---

    def acquire(self, browser_type="chrome", video=False, profile=None, record=False):
        """
        Acquire a browser session using ATOMIC LUA SCRIPTING.
        """
        profile_id = None
        if profile is True:
            profile_store = os.path.join(os.getcwd(), ".iso_profiles")
            if not os.path.exists(profile_store): os.makedirs(profile_store)
            id_file = os.path.join(profile_store, "default_profile.id")
            if os.path.exists(id_file):
                with open(id_file, "r") as f: profile_id = f.read().strip()
            else:
                profile_id = f"user_{uuid.uuid4().hex[:8]}"
                with open(id_file, "w") as f: f.write(profile_id)
        elif isinstance(profile, str):
            profile_id = profile

        self._init_sent = False
        
        # --- LUA SCRIPT ---
        lua_script = """
        local workers = redis.call('SMEMBERS', KEYS[1])
        for i = #workers, 2, -1 do
            local j = math.random(i)
            workers[i], workers[j] = workers[j], workers[i]
        end
        
        for _, worker in ipairs(workers) do
            local free_key = ARGV[1] .. worker .. ':' .. ARGV[2] .. ':free'
            local bid = redis.call('SPOP', free_key)
            if bid then
                local busy_key = ARGV[1] .. worker .. ':' .. ARGV[2] .. ':busy'
                redis.call('SADD', busy_key, bid)
                return {worker, bid}
            end
        end
        return nil
        """
        
        try:
            result = self.r.eval(lua_script, 1, WORKERS_SET, REDIS_PREFIX, browser_type)
        except Exception as e:
            raise BrowserError(f"Redis Lua Error: {e}")

        if result:
            worker_name = result[0]
            bid = result[1]
            
            self.session = {
                "browser_id": bid,
                "worker": worker_name,
                "browser_type": browser_type,
                "video": video,
                "profile_id": profile_id,
                "record": record
            }

            if profile_id or video or record:
                logger.info(f"[SDK] Initializing persistent environment on {worker_name}...")
                self._send("get_title") 
            
            return {"status": "ok", "browser_id": bid, "worker": worker_name}

        raise BrowserError(f"No browsers available for type: '{browser_type}'. Check workers.")

    def release(self):
        if not self.session: return {"status": "error", "error": "not_acquired"}
        try:
            if self.session.get("video"):
                logger.info("[SDK] Stopping video...")
                res = self._send("stop_video", timeout=120)
                if "video_url" in res:
                    self.video_url = res["video_url"]
                    logger.info(f"[SDK] Session Video: {self.video_url}")
            
            if self.session.get("record"):
                logger.info("[SDK] Finalizing session record (RRWeb)...")
                res_r = self._send("stop_record", timeout=60)
                if "record_url" in res_r:
                    self.record_url = res_r["record_url"]
                    logger.info(f"[SDK] Session Record URL: {self.record_url}")

            logger.info("[SDK] Sending release command...")
            res = self._send("release_browser")
            self.session_data = res
            return res
        except Exception as e:
            logger.error(f"[SDK ERROR] Error inside release: {e}")
            return {"status": "error", "error": str(e)}
        finally:
            self.session = None

    def _send(self, action, args={}, timeout=60):
        if not self.session: raise BrowserError(f"Cannot perform action '{action}': Browser session not acquired.")
        
        task_id = uuid.uuid4().hex
        result_key = f"{REDIS_PREFIX}result:{task_id}"
        queue = f"{REDIS_PREFIX}{self.session['worker']}:tasks"
        
        payload = {
            "task_id": task_id,
            "browser_id": self.session["browser_id"],
            "worker_name": self.session["worker"],
            "action": action,
            "args": args,
            "result_key": result_key
        }
        
        if not self._init_sent:
            if self.session.get("video"): payload["video"] = True
            if self.session.get("record"): payload["record"] = True
            if self.session.get("profile_id"):
                payload["profile_id"] = self.session["profile_id"]
                payload["browser_type"] = self.session["browser_type"]
        
        self._r_rpush(queue, json.dumps(payload))
        
        try:
            # OPTIMIZATION: Blocking Pop (Instant RPC)
            resp = self.r.blpop(result_key, timeout=timeout)
            if resp:
                self._init_sent = True
                return json.loads(resp[1])
            else:
                return {"status": "error", "error": "Timeout waiting for worker"}
        except Exception as e:
            return {"status": "error", "error": f"Redis RPC Error: {e}"}

    # --- Assertions Handler ---
    def _handle_assertion(self, action, args):
        if "screenshot" not in args: args["screenshot"] = True
        res = self._send(action, args)
        
        if res.get("status") == "fail":
            if "screenshot_base64" in res:
                try:
                    os.makedirs(ASSERTION_FOLDER, exist_ok=True)
                    selector_clean = args.get("selector", "unknown").replace("#", "").replace(".", "").replace(" ", "_")[:20]
                    timestamp = datetime.datetime.now().strftime("%H%M%S")
                    filename = f"FAIL_{action}_{selector_clean}_{timestamp}.png"
                    path = os.path.join(ASSERTION_FOLDER, filename)
                    with open(path, "wb") as f: f.write(base64.b64decode(res["screenshot_base64"]))
                    logger.warning(f"[Assertion Fail] Screenshot saved: {path}")
                except: pass
            error_msg = res.get("error", "Unknown assertion error")
            raise AssertionError(error_msg)
        return True

    # --- Helper: Save Base64 File (Clean) ---
    def _save_base64_file(self, res, key_name, output_path):
        if res.get("status") == "ok" and key_name in res:
            try:
                # Ensure dir exists
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(res[key_name]))
                return {"status": "ok", "path": os.path.abspath(output_path)}
            except Exception as e:
                return {"status": "error", "error": f"Failed to save local file: {e}"}
        return res

    # ==================================================
    # 1. NAVIGATION & LIFECYCLE
    # ==================================================
    def open_url(self, url): return self._send("open_url", {"url": url})
    def reload(self, ignore_cache=True, script=None): return self._send("reload", {"ignore_cache": ignore_cache, "script_to_evaluate_on_load": script})
    def refresh(self): return self._send("refresh")
    def go_back(self): return self._send("go_back")
    def go_forward(self): return self._send("go_forward")
    def internalize_links(self): return self._send("internalize_links")
    def get_navigation_history(self): return self._send("get_navigation_history")
    
    # ==================================================
    # 2. MOUSE INTERACTION
    # ==================================================
    def click(self, selector, timeout=None): return self._send("click", {"selector": selector, "timeout": timeout})
    def click_if_visible(self, selector): return self._send("click_if_visible", {"selector": selector})
    def click_visible_elements(self, selector, limit=0): return self._send("click_visible_elements", {"selector": selector, "limit": limit})
    def click_nth_element(self, selector, number=1): return self._send("click_nth_element", {"selector": selector, "number": number})
    def click_nth_visible_element(self, selector, number=1): return self._send("click_nth_visible_element", {"selector": selector, "number": number})
    def click_link(self, text): return self._send("click_link", {"text": text})
    def click_active_element(self): return self._send("click_active_element")
    def mouse_click(self, selector): return self._send("mouse_click", {"selector": selector})
    def nested_click(self, parent_selector, selector): return self._send("nested_click", {"parent_selector": parent_selector, "selector": selector})
    def click_with_offset(self, selector, x, y, center=False): return self._send("click_with_offset", {"selector": selector, "x": x, "y": y, "center": center})

    # ==================================================
    # 3. KEYBOARD & INPUT
    # ==================================================
    def type(self, selector, text, timeout=None): return self._send("type", {"selector": selector, "text": text, "timeout": timeout})
    def press_keys(self, selector, text): return self._send("press_keys", {"selector": selector, "text": text})
    def send_keys(self, selector, text): return self._send("send_keys", {"selector": selector, "text": text})
    def set_value(self, selector, text): return self._send("set_value", {"selector": selector, "text": text})
    def clear(self, selector): return self._send("clear", {"selector": selector})
    def clear_input(self, selector): return self._send("clear_input", {"selector": selector})
    def submit(self, selector): return self._send("submit", {"selector": selector})
    def focus(self, selector): return self._send("focus", {"selector": selector})
    
    # ==================================================
    # 4. GUI ACTIONS (PyAutoGUI / Profiled)
    # ==================================================
    def gui_click_element(self, selector, timeframe=0.25): return self._send("gui_click_element", {"selector": selector, "timeframe": timeframe})
    def gui_click_x_y(self, x, y, timeframe=0.25): return self._send("gui_click_x_y", {"x": x, "y": y, "timeframe": timeframe})
    def gui_click_captcha(self): return self._send("gui_click_captcha")
    def solve_captcha(self): return self._send("solve_captcha")
    def gui_drag_and_drop(self, drag_selector, drop_selector, timeframe=0.35): return self._send("gui_drag_and_drop", {"drag_selector": drag_selector, "drop_selector": drop_selector, "timeframe": timeframe})
    def gui_hover_element(self, selector): return self._send("gui_hover_element", {"selector": selector})
    def gui_write(self, text): return self._send("gui_write", {"text": text})
    def gui_press_keys(self, keys_list): return self._send("gui_press_keys", {"keys": keys_list})
    
    # ==================================================
    # 5. SELECTS & DROPDOWNS
    # ==================================================
    def select_option_by_text(self, selector, text): return self._send("select_option_by_text", {"selector": selector, "text": text})
    def select_option_by_value(self, selector, value): return self._send("select_option_by_value", {"selector": selector, "value": value})
    def select_option_by_index(self, selector, index): return self._send("select_option_by_index", {"selector": selector, "index": index})
    
    # ==================================================
    # 6. WINDOW & TAB MANAGEMENT
    # ==================================================
    def open_new_tab(self, url): return self._send("open_new_tab", {"url": url})
    def open_new_window(self, url): return self._send("open_new_window", {"url": url})
    def switch_to_tab(self, index=-1): return self._send("switch_to_tab", {"index": index})
    def switch_to_window(self, index=-1): return self._send("switch_to_window", {"index": index})
    def close_active_tab(self): return self._send("close_active_tab")
    def maximize(self): return self._send("maximize")
    def minimize(self): return self._send("minimize")
    def medimize(self): return self._send("medimize")
    def tile_windows(self): return self._send("tile_windows")
    
    # ==================================================
    # 7. DATA EXTRACTION (GETTERS)
    # ==================================================
    def get_text(self, selector="body"): return self._send("get_text", {"selector": selector})
    def get_title(self): return self._send("get_title")
    def get_current_url(self): return self._send("get_current_url")
    def get_page_source(self): return self._send("get_page_source")
    def get_html(self, selector=None): return self._send("get_html", {"selector": selector})
    def get_attribute(self, selector, attribute): return self._send("get_attribute", {"selector": selector, "attribute": attribute})
    def get_element_attributes(self, selector): return self._send("get_element_attributes", {"selector": selector})
    def get_user_agent(self): return self._send("get_user_agent")
    def get_cookie_string(self): return self._send("get_cookie_string")
    def get_element_rect(self, selector): return self._send("get_element_rect", {"selector": selector})
    def get_window_rect(self): return self._send("get_window_rect")
    def get_screen_rect(self): return self._send("get_screen_rect")
    def is_element_visible(self, selector): return self._send("is_element_visible", {"selector": selector})
    def is_text_visible(self, text): return self._send("is_text_visible", {"text": text})
    def is_checked(self, selector): return self._send("is_checked", {"selector": selector})
    def is_selected(self, selector): return self._send("is_selected", {"selector": selector})
    def is_online(self): return self._send("is_online")
    
    # ==================================================
    # 8. COOKIES & STORAGE
    # ==================================================
    def get_all_cookies(self): return self._send("get_all_cookies")
    def add_cookie(self, cookie_dict): return self._send("add_cookie", {"cookie": cookie_dict})
    def delete_cookie(self, name): return self._send("delete_cookie", {"name": name})
    def save_cookies(self, name="cookies.txt"):
        res = self._send("save_cookies")
        if res.get("status") == "ok" and "cookies" in res:
            try:
                with open(name, "w") as f: json.dump(res["cookies"], f, indent=4)
                return {"status": "ok", "path": os.path.abspath(name)}
            except Exception as e: return {"status": "error", "error": f"Failed to write local file: {e}"}
        return res
    def load_cookies(self, name="cookies.txt", cookies_list=None):
        final_cookies = cookies_list
        if not final_cookies and name:
            try:
                if os.path.exists(name):
                    with open(name, "r") as f: final_cookies = json.load(f)
                else: return {"status": "error", "error": f"Local cookie file not found: {name}"}
            except Exception as e: return {"status": "error", "error": f"Failed to read local file: {e}"}
        return self._send("load_cookies", {"name": name, "cookies": final_cookies})
    def clear_cookies(self): return self._send("clear_cookies")
    def get_local_storage_item(self, key): return self._send("get_local_storage_item", {"key": key})
    def set_local_storage_item(self, key, value): return self._send("set_local_storage_item", {"key": key, "value": value})
    def get_session_storage_item(self, key): return self._send("get_session_storage_item", {"key": key})
    def set_session_storage_item(self, key, value): return self._send("set_session_storage_item", {"key": key, "value": value})
    
    # ==================================================
    # 9. VISUALS & HIGHLIGHTS
    # ==================================================
    def highlight(self, selector): return self._send("highlight", {"selector": selector})
    def highlight_overlay(self, selector): return self._send("highlight_overlay", {"selector": selector})
    def remove_element(self, selector): return self._send("remove_element", {"selector": selector})
    def flash(self, selector, duration=1): return self._send("flash", {"selector": selector, "duration": duration})
    
    # ==================================================
    # 10. ADVANCED (MFA, Permissions, Scripting)
    # ==================================================
    def get_mfa_code(self, totp_key): return self._send("get_mfa_code", {"totp_key": totp_key})
    def enter_mfa_code(self, selector, totp_key): return self._send("enter_mfa_code", {"selector": selector, "totp_key": totp_key})
    def grant_permissions(self, permissions): return self._send("grant_permissions", {"permissions": permissions})
    def execute_script(self, script): return self._send("execute_script", {"script": script})
    def evaluate(self, expression): return self._send("evaluate", {"expression": expression})

    # ==================================================
    # 11. ASSERTIONS
    # ==================================================
    def assert_text(self, text, selector="html", screenshot=True): return self._handle_assertion("assert_text", {"text": text, "selector": selector, "screenshot": screenshot})
    def assert_exact_text(self, text, selector="html", screenshot=True): return self._handle_assertion("assert_exact_text", {"text": text, "selector": selector, "screenshot": screenshot})
    def assert_element(self, selector, screenshot=True): return self._handle_assertion("assert_element", {"selector": selector, "screenshot": screenshot})
    def assert_element_present(self, selector, screenshot=True): return self._handle_assertion("assert_element_present", {"selector": selector, "screenshot": screenshot})
    def assert_element_absent(self, selector, screenshot=True): return self._handle_assertion("assert_element_absent", {"selector": selector, "screenshot": screenshot})
    def assert_element_not_visible(self, selector, screenshot=True): return self._handle_assertion("assert_element_not_visible", {"selector": selector, "screenshot": screenshot})
    def assert_text_not_visible(self, text, selector="html", screenshot=True): return self._handle_assertion("assert_text_not_visible", {"text": text, "selector": selector, "screenshot": screenshot})
    def assert_title(self, title, screenshot=True): return self._handle_assertion("assert_title", {"title": title, "screenshot": screenshot})
    def assert_url(self, url_substring, screenshot=True): return self._handle_assertion("assert_url", {"url": url_substring, "screenshot": screenshot})
    def assert_attribute(self, selector, attribute, value, screenshot=True): return self._handle_assertion("assert_attribute", {"selector": selector, "attribute": attribute, "value": value, "screenshot": screenshot})
    
    # ==================================================
    # 12. SCROLLING & WAITING
    # ==================================================
    def scroll_into_view(self, selector): return self._send("scroll_into_view", {"selector": selector})
    def scroll_to_bottom(self): return self._send("scroll_to_bottom")
    def scroll_to_top(self): return self._send("scroll_to_top")
    def scroll_down(self, amount=25): return self._send("scroll_down", {"amount": amount})
    def scroll_up(self, amount=25): return self._send("scroll_up", {"amount": amount})
    def scroll_to_y(self, y): return self._send("scroll_to_y", {"y": y})
    def sleep(self, seconds): return self._send("sleep", {"seconds": seconds})
    def wait_for_element(self, selector, timeout=None): return self._send("wait_for_element", {"selector": selector, "timeout": timeout})
    def wait_for_text(self, text, selector="html", timeout=None): return self._send("wait_for_text", {"text": text, "selector": selector, "timeout": timeout})
    def wait_for_element_present(self, selector, timeout=None): return self._send("wait_for_element_present", {"selector": selector, "timeout": timeout})
    def wait_for_element_absent(self, selector, timeout=None): return self._send("wait_for_element_absent", {"selector": selector, "timeout": timeout})
    def wait_for_element_not_visible(self, selector, timeout=None): return self._send("wait_for_element_not_visible", {"selector": selector, "timeout": timeout})
    
    # ==================================================
    # 13. SCREENSHOTS & FILES
    # ==================================================
    def screenshot(self, filename=None, selector=None):
        if filename is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = uuid.uuid4().hex[:4]
            filename = os.path.join(SCREENSHOT_FOLDER, f"{timestamp}_{unique_id}.png")
        
        res = self._send("save_screenshot", {"name": "temp.png", "selector": selector})
        return self._save_base64_file(res, "image_base64", filename)
    
    def save_as_pdf(self, filename=None):
        if filename is None: filename = f"doc_{int(time.time())}.pdf"
        res = self._send("save_as_pdf")
        return self._save_base64_file(res, "pdf_base64", filename)

    def save_page_source(self, name="source.html"):
        res = self._send("save_page_source")
        if res.get("status") == "ok" and "source_base64" in res:
            try:
                data = base64.b64decode(res["source_base64"]).decode("utf-8")
                with open(name, "w", encoding="utf-8") as f: f.write(data)
                return {"status": "ok", "path": os.path.abspath(name)}
            except Exception as e: return {"status": "error", "error": str(e)}
        return res

    def upload_file(self, selector, local_file_path): 
        if not os.path.exists(local_file_path): return {"status": "error", "error": f"Local file not found: {local_file_path}"}
        with open(local_file_path, "rb") as f: file_data = base64.b64encode(f.read()).decode("utf-8")
        filename = os.path.basename(local_file_path)
        return self._send("upload_file", {"selector": selector, "file_name": filename, "file_data": file_data})
    
    # ==================================================
    # 14. NETWORK CONTROL
    # ==================================================
    def block_urls(self, patterns): return self._send("block_urls", {"patterns": patterns})
    def wait_for_network_idle(self): return self._send("wait_for_network_idle")
    def get_performance_metrics(self): return self._send("get_performance_metrics")
    
    # ==================================================
    # 15. IFRAME SWITCHING
    # ==================================================
    def switch_to_frame(self, selector): return self._send("switch_to_frame", {"selector": selector})
    def switch_to_default_content(self): return self._send("switch_to_default_content")
    def switch_to_parent_frame(self): return self._send("switch_to_parent_frame")
    
    # ==================================================
    # 16. ALERTS & DIALOGS
    # ==================================================
    def accept_alert(self): return self._send("accept_alert")
    def dismiss_alert(self): return self._send("dismiss_alert")
    def get_alert_text(self): return self._send("get_alert_text")
    
    # ==================================================
    # 17. ADVANCED MOUSE (DOM LEVEL)
    # ==================================================
    def double_click(self, selector): return self._send("double_click", {"selector": selector})
    def right_click(self, selector): return self._send("right_click", {"selector": selector})
    def hover(self, selector): return self._send("hover", {"selector": selector})
    def drag_and_drop(self, drag_selector, drop_selector): return self._send("drag_and_drop", {"drag_selector": drag_selector, "drop_selector": drop_selector})
    
    # ==================================================
    # 18. VIEWPORT SIZE
    # ==================================================
    def set_window_size(self, width, height): return self._send("set_window_size", {"width": width, "height": height})
    def set_window_rect(self, x, y, width, height): return self._send("set_window_rect", {"x": x, "y": y, "width": width, "height": height})
    
    # ==================================================
    # 19. SESSION STATE (Import/Export)
    # ==================================================
    def export_session(self): return self._send("get_storage_state")
    def import_session(self, state_dict): return self._send("set_storage_state", {"state": state_dict})
    
    # ==================================================
    # 20. GOD MODE (Raw CDP Access)
    # ==================================================
    def execute_cdp_cmd(self, cmd, params={}): return self._send("execute_cdp_cmd", {"cmd": cmd, "params": params})