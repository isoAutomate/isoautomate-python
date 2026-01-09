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
# By default, do not output anything unless the user configures it
logger.addHandler(logging.NullHandler())

# --- Robust .env Loading ---
def _load_package_env(custom_path=None):
    # 1. If user provided a specific path, load that first
    if custom_path and os.path.exists(custom_path):
        load_dotenv(dotenv_path=custom_path, override=True)
        return

    # 2. Check current working directory for standard .env
    cwd_env = os.path.join(os.getcwd(), '.env')
    if os.path.exists(cwd_env):
        load_dotenv(dotenv_path=cwd_env)

    # 3. Check where the user's entry-point script is located
    try:
        if hasattr(__main__, "__file__"):
            main_script_dir = os.path.dirname(os.path.abspath(__main__.__file__))
            main_env = os.path.join(main_script_dir, '.env')
            if main_env != cwd_env and os.path.exists(main_env):
                load_dotenv(dotenv_path=main_env)
    except Exception:
        pass # Fallback for interactive shells/notebooks

# Automatically load env vars if present
_load_package_env()

class BrowserClient:
    """
    Python SDK for isoAutomate.
    Controls remote browsers via Redis queues.
    Fully synchronized with SeleniumBase CDP Mode capabilities.
    """

    def __init__(self, redis_url=None, redis_host=None, redis_port=None, redis_password=None, redis_db=None, redis_ssl=False, env_file=None):
        
        if env_file:
            _load_package_env(custom_path=env_file)
            
        # Check Environment
        env_url = os.getenv("REDIS_URL")
        env_host = os.getenv("REDIS_HOST")
        env_port = os.getenv("REDIS_PORT")
        env_pass = os.getenv("REDIS_PASSWORD")
        env_db = os.getenv("REDIS_DB")
        env_ssl = os.getenv("REDIS_SSL", "False").lower() in ("true", "1", "yes")

        # Final Config
        self.redis_url = redis_url or env_url
        self.host = redis_host or env_host
        self.port = redis_port or env_port
        self.password = redis_password or env_pass
        self.db = redis_db if redis_db is not None else (env_db or 0)
        self.ssl = redis_ssl or env_ssl
        
        # STRICT VALIDATION: we have enough info to connect?
        if not self.redis_url and not self.host:
            raise BrowserError(
                "Missing Redis Configuration. You must provide a 'redis_url' or 'redis_host'. "
                "Check your .env file or pass them directly to BrowserClient()."
            )

        # Create Connection
        try:
            if self.redis_url:
                self.r = redis.Redis.from_url(
                    self.redis_url, 
                    decode_responses=True
                )
            else:
                # Ensure port is an integer
                actual_port = int(self.port) if self.port else 6379
                self.r = redis.Redis(
                    host=self.host,
                    port=actual_port,
                    password=self.password,
                    db=int(self.db),
                    decode_responses=True,
                    ssl=self.ssl,
                    ssl_cert_reqs=None
                )
        except Exception as e:
            raise BrowserError(f"Failed to initialize Redis connection: {e}")

        self.session = None
        self.video_url = None
        self.session_data = {}

    # ---------------------------- Internal Redis Wrappers ----------------------------
    
    @redis_retry()
    def _r_smembers(self, key): return self.r.smembers(key)

    @redis_retry()
    def _r_spop(self, key): return self.r.spop(key)

    @redis_retry()
    def _r_sadd(self, key, *values): return self.r.sadd(key, *values)

    @redis_retry()
    def _r_rpush(self, key, *values): return self.r.rpush(key, *values)

    @redis_retry()
    def _r_get(self, key): return self.r.get(key)

    @redis_retry()
    def _r_delete(self, key): return self.r.delete(key)

    # ---------------------------- Context Manager ----------------------------

    def __enter__(self):
        self.video_url = None
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
    
    # ---------------------------- Connection & Lifecycle ----------------------------

    def acquire(self, browser_type="chrome", video=False, profile=None):
        """
        Acquire a browser session. 
        :param browser_type: The engine to use (e.g., chrome, chrome_profiled).
        :param video: Set True to capture the session video in MP4 format.
        :param profile: String for custom persistence, or True for default project persistence.
        """
        # --- PROFILE ID LOGIC ---
        profile_id = None
        if profile is True:
            # Auto-manage a persistent ID for this local project folder
            profile_store = os.path.join(os.getcwd(), ".iso_profiles")
            if not os.path.exists(profile_store):
                os.makedirs(profile_store)
            
            id_file = os.path.join(profile_store, "default_profile.id")
            if os.path.exists(id_file):
                with open(id_file, "r") as f:
                    profile_id = f.read().strip()
            else:
                profile_id = f"user_{uuid.uuid4().hex[:8]}"
                with open(id_file, "w") as f:
                    f.write(profile_id)
        elif isinstance(profile, str):
            profile_id = profile

        # Track if the infrastructure setup (profile/video) has been sent to the worker
        self._init_sent = False
        
        workers = list(self._r_smembers(WORKERS_SET))
        random.shuffle(workers)
        
        for worker_name in workers:
            # Try to pop a free browser
            bid = self._r_spop(f"{REDIS_PREFIX}{worker_name}:{browser_type}:free")
            
            if bid:
                # Mark as busy
                self._r_sadd(f"{REDIS_PREFIX}{worker_name}:{browser_type}:busy", bid)
                
                self.session = {
                    "browser_id": bid,
                    "worker": worker_name,
                    "browser_type": browser_type,
                    "video": video,
                    "profile_id": profile_id 
                }

                if profile_id or video:
                    logger.info(f"[SDK] Initializing persistent environment on {worker_name}...")
                    self._send("get_title")
                else:
                    # For standard browsers, we can either skip or send a faster 'is_online' check
                    # But 'get_title' is already very fast.
                    pass
                
                return {"status": "ok", "browser_id": bid, "worker": worker_name}

        raise BrowserError(f"No browsers available for type: '{browser_type}'. Check if your workers are running and the type is correct.")

    def release(self):
        if not self.session: 
            return {"status": "error", "error": "not_acquired"}
        
        try:
            # --- STOP Video SIGNAL ---
            if self.session.get("video"):
                logger.info("[SDK] Stopping video...")
                res = self._send("stop_video", timeout=120)
                
                if "video_url" in res:
                    self.video_url = res["video_url"]
                    logger.info(f"[SDK] Session Video: {self.video_url}")

            # Standard Release
            logger.info("[SDK] Sending release command...")
            res = self._send("release_browser")
            
            self.session_data = res
            
            logger.info(f"[SDK] Release result: {res}")
            return res
        
        except Exception as e:
            logger.error(f"[SDK ERROR] Error inside release: {e}")
            return {"status": "error", "error": str(e)}
        
        finally:
            self.session = None

    def _send(self, action, args={}, timeout=60):
        if not self.session: 
            raise BrowserError(f"Cannot perform action '{action}': Browser session not acquired.")
        
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
        
        # Only attach infrastructure flags if they haven't been processed by the worker yet
        if not self._init_sent:
            if self.session.get("video"):
                payload["video"] = True
            
            if self.session.get("profile_id"):
                payload["profile_id"] = self.session["profile_id"]
                payload["browser_type"] = self.session["browser_type"]
        
        self._r_rpush(queue, json.dumps(payload))
        
        start = time.time()
        while time.time() - start < timeout:
            res = self._r_get(result_key)
            if res:
                # Once we get a successful result back, we know the worker has initialized 
                # the profile/video state for this session.
                self._init_sent = True
                self._r_delete(result_key)
                return json.loads(res)
            time.sleep(0.05)
            
        return {"status": "error", "error": "Timeout waiting for worker"}

    # ---------------------------- ASSERTION HANDLER ----------------------------
    def _handle_assertion(self, action, args):
        """
        Special wrapper for assertions to handle auto-screenshots and Python Exception raising.
        """
        # Ensure 'screenshot' flag is passed (Default True)
        if "screenshot" not in args:
            args["screenshot"] = True
            
        res = self._send(action, args)
        
        if res.get("status") == "fail":
            # 1. Check for Screenshot
            if "screenshot_base64" in res:
                try:
                    os.makedirs(ASSERTION_FOLDER, exist_ok=True)
                    
                    # Create a nice filename: action_selector_timestamp.png
                    selector_clean = args.get("selector", "unknown").replace("#", "").replace(".", "").replace(" ", "_")[:20]
                    timestamp = datetime.datetime.now().strftime("%H%M%S")
                    filename = f"FAIL_{action}_{selector_clean}_{timestamp}.png"
                    path = os.path.join(ASSERTION_FOLDER, filename)
                    
                    with open(path, "wb") as f:
                        f.write(base64.b64decode(res["screenshot_base64"]))
                        
                    logger.warning(f"[Assertion Fail] Screenshot saved: {path}")
                except Exception as e:
                    logger.error(f"[SDK Error] Failed to save failure screenshot: {e}")

            # 2. Raise Python Error (So the test script fails)
            error_msg = res.get("error", "Unknown assertion error")
            raise AssertionError(error_msg)
            
        return True

    # =========================================================================
    #  ACTION METHODS (Mapped to browser_actions.py)
    # =========================================================================

    # --- 1. Navigation & Setup ---
    def open_url(self, url):
        return self._send("open_url", {"url": url})
    
    def reload(self, ignore_cache=True, script=None):
        return self._send("reload", {"ignore_cache": ignore_cache, "script_to_evaluate_on_load": script})

    def refresh(self): return self._send("refresh")
    def go_back(self): return self._send("go_back")
    def go_forward(self): return self._send("go_forward")
    
    def internalize_links(self):
        """Forces new tab links to open in the current tab."""
        return self._send("internalize_links")

    def get_navigation_history(self):
        return self._send("get_navigation_history")

    # --- 2. Mouse Interaction ---
    def click(self, selector, timeout=None):
        return self._send("click", {"selector": selector, "timeout": timeout})

    def click_if_visible(self, selector):
        return self._send("click_if_visible", {"selector": selector})
    
    def click_visible_elements(self, selector, limit=0):
        return self._send("click_visible_elements", {"selector": selector, "limit": limit})

    def click_nth_element(self, selector, number=1):
        return self._send("click_nth_element", {"selector": selector, "number": number})
    
    def click_nth_visible_element(self, selector, number=1):
        return self._send("click_nth_visible_element", {"selector": selector, "number": number})

    def click_link(self, text):
        return self._send("click_link", {"text": text})

    def click_active_element(self):
        return self._send("click_active_element")

    def mouse_click(self, selector):
        return self._send("mouse_click", {"selector": selector})

    def nested_click(self, parent_selector, selector):
        return self._send("nested_click", {"parent_selector": parent_selector, "selector": selector})

    def click_with_offset(self, selector, x, y, center=False):
        return self._send("click_with_offset", {"selector": selector, "x": x, "y": y, "center": center})

    # --- 3. Keyboard & Input ---
    def type(self, selector, text, timeout=None):
        return self._send("type", {"selector": selector, "text": text, "timeout": timeout})

    def press_keys(self, selector, text):
        """Human-like typing into a specific element."""
        return self._send("press_keys", {"selector": selector, "text": text})

    def send_keys(self, selector, text):
        return self._send("send_keys", {"selector": selector, "text": text})

    def set_value(self, selector, text):
        return self._send("set_value", {"selector": selector, "text": text})

    def clear(self, selector):
        return self._send("clear", {"selector": selector})
    
    def clear_input(self, selector):
        return self._send("clear_input", {"selector": selector})
    
    def submit(self, selector):
        return self._send("submit", {"selector": selector})

    def focus(self, selector):
        return self._send("focus", {"selector": selector})

    # --- 4. GUI / Profiled (PyAutoGUI) ---
    def gui_click_element(self, selector, timeframe=0.25):
        """Real OS-level mouse click on an element (Bypasses Bots)."""
        return self._send("gui_click_element", {"selector": selector, "timeframe": timeframe})

    def gui_click_x_y(self, x, y, timeframe=0.25):
        return self._send("gui_click_x_y", {"x": x, "y": y, "timeframe": timeframe})

    def gui_click_captcha(self):
        """Locates and clicks the verification checkbox within challenge iframes."""
        return self._send("gui_click_captcha")

    def solve_captcha(self):
        """Triggers the automated handling of verification checkboxes."""
        return self._send("solve_captcha")

    def gui_drag_and_drop(self, drag_selector, drop_selector, timeframe=0.35):
        return self._send("gui_drag_and_drop", {"drag_selector": drag_selector, "drop_selector": drop_selector, "timeframe": timeframe})

    def gui_hover_element(self, selector):
        return self._send("gui_hover_element", {"selector": selector})

    def gui_write(self, text):
        """OS-level keyboard typing (No selector needed, types where focused)."""
        return self._send("gui_write", {"text": text})

    def gui_press_keys(self, keys_list):
        return self._send("gui_press_keys", {"keys": keys_list})

    # --- 5. Selects & Dropdowns ---
    def select_option_by_text(self, selector, text):
        return self._send("select_option_by_text", {"selector": selector, "text": text})

    def select_option_by_value(self, selector, value):
        return self._send("select_option_by_value", {"selector": selector, "value": value})

    def select_option_by_index(self, selector, index):
        return self._send("select_option_by_index", {"selector": selector, "index": index})

    # --- 6. Window & Tab Management ---
    def open_new_tab(self, url):
        return self._send("open_new_tab", {"url": url})
    
    def open_new_window(self, url):
        return self._send("open_new_window", {"url": url})

    def switch_to_tab(self, index=-1):
        """0 is oldest, -1 is newest."""
        return self._send("switch_to_tab", {"index": index})
    
    def switch_to_window(self, index=-1):
        return self._send("switch_to_window", {"index": index})

    def close_active_tab(self):
        return self._send("close_active_tab")

    def maximize(self): return self._send("maximize")
    def minimize(self): return self._send("minimize")
    def medimize(self): return self._send("medimize")
    def tile_windows(self): return self._send("tile_windows")

    # --- 7. Data Extraction (Getters) ---
    def get_text(self, selector="body"):
        return self._send("get_text", {"selector": selector})

    def get_title(self):
        return self._send("get_title")

    def get_current_url(self):
        return self._send("get_current_url")

    def get_page_source(self):
        return self._send("get_page_source")

    def get_html(self, selector=None):
        """Gets inner HTML. If selector provided, gets that element's HTML."""
        return self._send("get_html", {"selector": selector})

    def get_attribute(self, selector, attribute):
        return self._send("get_attribute", {"selector": selector, "attribute": attribute})
    
    def get_element_attributes(self, selector):
        return self._send("get_element_attributes", {"selector": selector})

    def get_user_agent(self):
        return self._send("get_user_agent")
    
    def get_cookie_string(self):
        return self._send("get_cookie_string")
    
    def get_element_rect(self, selector):
        return self._send("get_element_rect", {"selector": selector})
    
    def get_window_rect(self): return self._send("get_window_rect")
    def get_screen_rect(self): return self._send("get_screen_rect")

    def is_element_visible(self, selector):
        return self._send("is_element_visible", {"selector": selector})
    
    def is_text_visible(self, text):
        return self._send("is_text_visible", {"text": text})

    def is_checked(self, selector):
        return self._send("is_checked", {"selector": selector})
    
    def is_selected(self, selector):
        return self._send("is_selected", {"selector": selector})

    def is_online(self):
        return self._send("is_online")
        
    def get_performance_metrics(self):
        """Returns network/performance metrics from the browser."""
        return self._send("get_performance_metrics")

    # --- 8. Cookies & Storage ---
    def get_all_cookies(self):
        return self._send("get_all_cookies")

    def save_cookies(self, name="cookies.txt"):
        """
        Retrieves cookies from the remote browser and saves them to a local file.
        """
        # 1. Ask engine for the data
        res = self._send("save_cookies")
        
        # 2. Check if we got cookies back
        if res.get("status") == "ok" and "cookies" in res:
            try:
                # 3. Save to LOCAL file on client machine
                with open(name, "w") as f:
                    json.dump(res["cookies"], f, indent=4)
                return {"status": "ok", "path": os.path.abspath(name)}
            except Exception as e:
                return {"status": "error", "error": f"Failed to write local file: {e}"}
        
        return res

    def load_cookies(self, name="cookies.txt", cookies_list=None):
        """
        Loads cookies. 
        - If 'cookies_list' is provided, it sends those.
        - If 'name' is provided, it READS the LOCAL file and sends the data.
        """
        final_cookies = cookies_list

        # If no list provided, try to read the local file
        if not final_cookies and name:
            try:
                if os.path.exists(name):
                    with open(name, "r") as f:
                        final_cookies = json.load(f)
                else:
                    return {"status": "error", "error": f"Local cookie file not found: {name}"}
            except Exception as e:
                return {"status": "error", "error": f"Failed to read local file: {e}"}

        # Send the actual data to the engine
        return self._send("load_cookies", {"name": name, "cookies": final_cookies})

    def clear_cookies(self):
        return self._send("clear_cookies")

    def get_local_storage_item(self, key):
        return self._send("get_local_storage_item", {"key": key})
    
    def set_local_storage_item(self, key, value):
        return self._send("set_local_storage_item", {"key": key, "value": value})
    
    def get_session_storage_item(self, key):
        return self._send("get_session_storage_item", {"key": key})
    
    def set_session_storage_item(self, key, value):
        return self._send("set_session_storage_item", {"key": key, "value": value})
        
    def export_session(self):
        """Retrieves Cookies, LocalStorage, and SessionStorage."""
        return self._send("get_storage_state")

    def import_session(self, state_dict):
        """Restores Cookies, LocalStorage, and SessionStorage."""
        return self._send("set_storage_state", {"state": state_dict})

    # --- 9. Visuals & Highlights ---
    def highlight(self, selector):
        return self._send("highlight", {"selector": selector})
    
    def highlight_overlay(self, selector):
        return self._send("highlight_overlay", {"selector": selector})
    
    def remove_element(self, selector):
        return self._send("remove_element", {"selector": selector})
    
    def flash(self, selector, duration=1):
        return self._send("flash", {"selector": selector, "duration": duration})

    # --- 10. Advanced (MFA, Permissions, Scripting) ---
    def get_mfa_code(self, totp_key):
        return self._send("get_mfa_code", {"totp_key": totp_key})

    def enter_mfa_code(self, selector, totp_key):
        return self._send("enter_mfa_code", {"selector": selector, "totp_key": totp_key})

    def grant_permissions(self, permissions):
        return self._send("grant_permissions", {"permissions": permissions})

    def execute_script(self, script):
        return self._send("execute_script", {"script": script})
    
    def evaluate(self, expression):
        return self._send("evaluate", {"expression": expression})
        
    def block_urls(self, patterns):
        """
        Blocks network requests matching the patterns.
        Example: ["*ads*", "*.png"]
        """
        return self._send("block_urls", {"patterns": patterns})

    # --- 11. Assertions (Commercial Grade with Screenshots) ---
    def assert_text(self, text, selector="html", screenshot=True):
        return self._handle_assertion("assert_text", {"text": text, "selector": selector, "screenshot": screenshot})
    
    def assert_exact_text(self, text, selector="html", screenshot=True):
        return self._handle_assertion("assert_exact_text", {"text": text, "selector": selector, "screenshot": screenshot})

    def assert_element(self, selector, screenshot=True):
        return self._handle_assertion("assert_element", {"selector": selector, "screenshot": screenshot})
    
    def assert_element_present(self, selector, screenshot=True):
        return self._handle_assertion("assert_element_present", {"selector": selector, "screenshot": screenshot})
    
    def assert_element_absent(self, selector, screenshot=True):
        return self._handle_assertion("assert_element_absent", {"selector": selector, "screenshot": screenshot})
    
    def assert_element_not_visible(self, selector, screenshot=True):
        return self._handle_assertion("assert_element_not_visible", {"selector": selector, "screenshot": screenshot})
    
    def assert_text_not_visible(self, text, selector="html", screenshot=True):
        return self._handle_assertion("assert_text_not_visible", {"text": text, "selector": selector, "screenshot": screenshot})

    def assert_title(self, title, screenshot=True):
        return self._handle_assertion("assert_title", {"title": title, "screenshot": screenshot})
        
    def assert_url(self, url_substring, screenshot=True):
        return self._handle_assertion("assert_url", {"url": url_substring, "screenshot": screenshot})

    def assert_attribute(self, selector, attribute, value, screenshot=True):
        return self._handle_assertion("assert_attribute", {"selector": selector, "attribute": attribute, "value": value, "screenshot": screenshot})

    # --- 12. Scrolling & Waiting ---
    def scroll_into_view(self, selector):
        return self._send("scroll_into_view", {"selector": selector})

    def scroll_to_bottom(self):
        return self._send("scroll_to_bottom")
    
    def scroll_to_top(self):
        return self._send("scroll_to_top")
    
    def scroll_down(self, amount=25): return self._send("scroll_down", {"amount": amount})
    def scroll_up(self, amount=25): return self._send("scroll_up", {"amount": amount})
    def scroll_to_y(self, y): return self._send("scroll_to_y", {"y": y})

    def sleep(self, seconds):
        return self._send("sleep", {"seconds": seconds})

    def wait_for_element(self, selector, timeout=None):
        return self._send("wait_for_element", {"selector": selector, "timeout": timeout})
    
    def wait_for_text(self, text, selector="html", timeout=None):
        return self._send("wait_for_text", {"text": text, "selector": selector, "timeout": timeout})
    
    def wait_for_element_present(self, selector, timeout=None):
        return self._send("wait_for_element_present", {"selector": selector, "timeout": timeout})
    
    def wait_for_element_absent(self, selector, timeout=None):
        return self._send("wait_for_element_absent", {"selector": selector, "timeout": timeout})
        
    def wait_for_network_idle(self):
        return self._send("wait_for_network_idle")

    # --- 13. Screenshots & Files ---
    def save_page_source(self, name="source.html"):
        """
        Gets the page source from the remote browser and saves it to a local file.
        """
        # 1. Send the request to the engine
        response = self._send("save_page_source")

        # 2. Check if the engine sent back the data
        if response.get("status") == "ok" and "source_base64" in response:
            try:
                # 3. Decode the data
                encoded_data = response["source_base64"]
                decoded_html = base64.b64decode(encoded_data).decode("utf-8")

                # 4. Save to local file on the Client machine
                with open(name, "w", encoding="utf-8") as f:
                    f.write(decoded_html)
                
                # Update response to indicate local success
                response["local_file_saved"] = True
                response["file_path"] = os.path.abspath(name)
                del response["source_base64"] # Clean up the huge string from the result dict
            
            except Exception as e:
                return {"status": "error", "error": f"Failed to save local file: {str(e)}"}
        
        return response

    def screenshot(self, filename=None, selector=None):
        """
        Takes a screenshot (optionally of a specific element) and saves it locally.
        """
        if filename is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = uuid.uuid4().hex[:4]
            default_filename = f"{timestamp}_{unique_id}.png"
            save_path = os.path.join(SCREENSHOT_FOLDER, default_filename)
        else:
            save_path = filename
        
        save_directory = os.path.dirname(save_path)
        if save_directory and not os.path.exists(save_directory):
            try: os.makedirs(save_directory)
            except: pass

        # Send command to worker (using temp name on worker side)
        res = self._send("save_screenshot", {"name": "temp.png", "selector": selector})
        
        if res.get("status") == "ok" and "image_base64" in res:
            try:
                with open(save_path, "wb") as f:
                    f.write(base64.b64decode(res["image_base64"]))
                return {"status": "ok", "path": os.path.abspath(save_path)}
            except Exception as e:
                return {"status": "error", "error": f"Failed to save local file: {e}"}
        
        return res
    
    def save_as_pdf(self, filename=None):
        """
        Generates a PDF of the page and saves it locally.
        """
        if filename is None:
            filename = f"doc_{int(time.time())}.pdf"
        
        # Ensure path exists
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)

        res = self._send("save_as_pdf")
        
        if res.get("status") == "ok" and "pdf_base64" in res:
            try:
                with open(filename, "wb") as f:
                    f.write(base64.b64decode(res["pdf_base64"]))
                return {"status": "ok", "path": os.path.abspath(filename)}
            except Exception as e:
                return {"status": "error", "error": f"Failed to save PDF: {e}"}
        
        return res
    
    # --- New File Upload ---
    def upload_file(self, selector, local_file_path): 
        """
        Reads a local file, encodes it, and sends it to the remote worker.
        """
        if not os.path.exists(local_file_path):
            return {"status": "error", "error": f"Local file not found: {local_file_path}"}

        with open(local_file_path, "rb") as f:
            file_data = base64.b64encode(f.read()).decode("utf-8")
        
        filename = os.path.basename(local_file_path)
        
        return self._send("upload_file", {
            "selector": selector, 
            "file_name": filename,
            "file_data": file_data
        })

    # --- New Mouse Actions ---
    def double_click(self, selector): return self._send("double_click", {"selector": selector})
    def right_click(self, selector): return self._send("right_click", {"selector": selector})
    def hover(self, selector): return self._send("hover", {"selector": selector})
    def drag_and_drop(self, drag_selector, drop_selector): return self._send("drag_and_drop", {"drag_selector": drag_selector, "drop_selector": drop_selector})

    # --- New Frame Actions ---
    def switch_to_frame(self, selector): return self._send("switch_to_frame", {"selector": selector})
    def switch_to_default_content(self): return self._send("switch_to_default_content")
    def switch_to_parent_frame(self): return self._send("switch_to_parent_frame")

    # --- New Alert Actions ---
    def accept_alert(self): return self._send("accept_alert")
    def dismiss_alert(self): return self._send("dismiss_alert")
    def get_alert_text(self): return self._send("get_alert_text")

    # --- New Granular Cookie Actions ---
    def add_cookie(self, cookie_dict): return self._send("add_cookie", {"cookie": cookie_dict})
    def delete_cookie(self, name): return self._send("delete_cookie", {"name": name})

    # --- New Viewport Action ---
    def set_window_size(self, width, height): return self._send("set_window_size", {"width": width, "height": height})
    def set_window_rect(self, x, y, width, height): return self._send("set_window_rect", {"x": x, "y": y, "width": width, "height": height})

    # --- New Wait Action ---
    def wait_for_element_not_visible(self, selector, timeout=None): return self._send("wait_for_element_not_visible", {"selector": selector, "timeout": timeout})