<div align="center">
  <h1 align="center">isoAutomate Python SDK</h1>
  
  <p align="center">
    <b>The Sovereign Browser Infrastructure & Orchestration Platform</b>
  </p>

  <a href="https://pypi.org/project/isoautomate/">
    <img src="https://img.shields.io/pypi/v/isoautomate.svg?color=blue" alt="PyPI version">
  </a>
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  </a>
  <a href="https://isoautomate.com/docs">
    <img src="https://img.shields.io/badge/isoAutomate-Official-blue.svg" alt="Documentation">
  </a>
  <a href="https://isoautomate.readthedocs.io/">
    <img src="https://img.shields.io/badge/Docs-ReadTheDocs-blue.svg" alt="ReadTheDocs">
  </a>
</div>

<br />

<div align="center">
<img src="ext/sdk-python.png" alt="isoAutomate Architecture" width="450" />
</div>

---

## Installation

Install the SDK via pip:

```bash
pip install isoautomate
```
## Configuration

The SDK requires a connection to a Redis instance to communicate with the browser engine. You can configure this either via an environment file (.env) or directly in your Python code.

**Method A: Environment Variables (.env)**
Create a .env file in your project root. This is the recommended way to keep credentials out of your source code. You can use either a single connection string or individual fields.

```ini
# Individual Fields
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=yourpassword
REDIS_DB=0
REDIS_SSL=False

# OR Single Redis URL (overrides individual fields if present)
# REDIS_URL=rediss://:password@host:port/0
```

**Method B: Direct Initialization**

You can pass connection details directly when creating the BrowserClient instance.

**Using individual arguments:**

```python
from isoautomate import BrowserClient

browser = BrowserClient(
    redis_host="localhost",
    redis_port=6379,
    redis_password="yourpassword",
    redis_db=0,
    redis_ssl=True
)
```

**Using a Redis URL:**

```python
from isoautomate import BrowserClient

browser = BrowserClient(
    redis_url="rediss://:password@host:port/0"
)
```

## Usage Examples

Browser sessions are managed through the `BrowserClient`. To ensure that browser resources are cleaned up properly on the server, we highly recommend using the Context Manager approach.

### 1. Context Manager (Recommended)

Using the `with` statement ensures that the browser is automatically released back to the fleet, even if your script crashes or an error occurs.

```python
from isoautomate import BrowserClient

# The context manager handles browser.release() automatically
with BrowserClient() as browser:
    # Acquire the browser instance
    browser.acquire(browser_type="chrome_profiled", record=True)
    
    browser.open_url("https://example.com")
    browser.assert_text("Example Domain")

# Video URL is available after the context block ends
print(f"Session recording: {browser.video_url}")
```

### 2. Manual Control

If you prefer manual control, you must call .release() inside a finally block to prevent "ghost" browsers from staying active on your worker(watchdog in the engine/worker makes the browsers available after 120s or the time you gave when starting the worker/engine).

```python
from isoautomate import BrowserClient

browser = BrowserClient()
try:
    browser.acquire(browser_type="chrome")
    browser.open_url("https://example.com")
finally:
    # Crucial: Always release to free up slots for other tasks
    browser.release()
```
## The Acquire Method

The `acquire()` method is used to claim a browser from your remote fleet. It supports several parameters to customize your environment.

### Parameters

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `browser_type` | `str` | `"chrome"` | The browser to use: `chrome`, `brave`, `opera`, or their `_profiled` variants for CDP mode. |
| `record` | `bool` | `False` | When True, starts an MP4 recording of the browser session. |
| `profile` | `str` \| `bool` | `None` | Enables persistence for cookies, logins, and site data. |

### Understanding Persistence (Profiles)

Persistence allows you to resume sessions so you don't have to log in to websites repeatedly.

- **Managed Profile (`profile=True`):** The SDK manages a persistent ID for you locally. It creates a `.iso_profiles` folder in your project to remember which browser belongs to this project.
- **Custom Profile (`profile="my_account_1"`):** You provide a specific string. This is best for managing multiple different accounts. Any script using the same string will share the same cookies and history.

```python
# Example: Using a named profile for a specific social media account
browser.acquire(profile="twitter_marketing_account")
```

## Browser Actions

Once you have acquired a browser, you can control it using the following methods.

### 1. Navigation

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `open_url(url)` | `url (str)` | Navigates the browser to the specified website. |
| `reload(ignore_cache, script)` | `ignore_cache (bool=True)`, `script (str=None)` | Force reloads the page. `ignore_cache` ensures a fresh fetch. `script` runs JS immediately after the reload. |
| `refresh()` | None | Reloads the current page (standard refresh). |
| `go_back()` | None | Navigates to the previous page in history. |
| `go_forward()` | None | Navigates to the next page in history. |
| `internalize_links()` | None | Forces all links on the current page to open in the current tab instead of a new one. |
| `get_navigation_history()` | None | Returns the list of URLs in the current session's history. |

```python
# Basic navigation
browser.open_url("https://isoautomate.com")

# Hard reload with a custom script to hide a banner
browser.reload(ignore_cache=True, script="document.querySelector('.banner').style.display='none';")

# Prevent new tabs from popping up
browser.internalize_links()
```

### 2. Mouse Interactions

These methods handle standard web-element interactions using the browser's automation engine.

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `click(selector, timeout)` | `selector (str)`, `timeout (int=None)` | Clicks an element. `timeout` (seconds) determines how long to wait for the element to appear. |
| `click_if_visible(selector)` | `selector (str)` | Attempts to click an element only if it is visible. Fails silently if not found. |
| `click_visible_elements(selector, limit)` | `selector (str)`, `limit (int=0)` | Clicks all visible instances of a selector. `limit` caps the clicks (0 for all). |
| `click_nth_visible_element(selector, number)` | `selector (str)`, `number (int=1)` | Clicks the specific visible instance (e.g., 2nd button) based on the `number` provided. |
| `click_link(text)` | `text (str)` | Finds a link by its visible text and clicks it. |
| `click_active_element()` | None | Clicks whichever element currently holds the browser's focus. |
| `double_click(selector)` | `selector (str)` | Performs a standard double-click on the targeted element. |
| `right_click(selector)` | `selector (str)` | Performs a context (right) click on the targeted element. |
| `hover(selector)` | `selector (str)` | Moves the mouse cursor over an element without clicking. |
| `nested_click(parent_selector, selector)` | `parent_selector (str)`, `selector (str)` | Finds the parent first, then locates and clicks the child element inside it. |
| `click_with_offset(selector, x, y, center)` | `selector (str)`, `x, y (int)`, `center (bool=False)` | Clicks at relative coordinates. If `center=True`, x/y are offsets from the element's middle. |
| `drag_and_drop(drag_selector, drop_selector)` | `drag_selector (str)`, `drop_selector (str)` | Picks up the first element and drops it onto the second element via JS events. |

```python
# Click a button but wait up to 15 seconds for it to load
browser.click("#submit-btn", timeout=15)

# Click the 2nd visible 'Add to Cart' button found on a page
browser.click_nth_visible_element("button.add-to-cart", number=2)

# Click exactly 5 pixels from the left and 10 pixels from the top of an element
browser.click_with_offset("#map-canvas", x=5, y=10)
```

### 3. Keyboard and Input

These methods provide granular control over how text is entered and how forms are processed, ranging from high-speed data entry to human-simulated typing.

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `type(selector, text, timeout)` | `selector, text (str)`, `timeout (int=None)` | Rapidly enters text into a field. Optional `timeout` waits for the field to appear. |
| `press_keys(selector, text)` | `selector, text (str)` | Simulates individual key presses. This is slower and mimics human behavior to avoid detection by basic scripts. |
| `send_keys(selector, text)` | `selector, text (str)` | Standard automation method to send raw keys to an element. |
| `set_value(selector, text)` | `selector, text (str)` | Directly sets the `value` attribute of an element via the browser's internal API. |
| `clear(selector)` | `selector (str)` | Deletes all current text/content within an input or textarea element. |
| `clear_input(selector)` | `selector (str)` | Specifically targets `<input>` fields to reset their state. |
| `submit(selector)` | `selector (str)` | Triggers the `submit` event for the form containing the specified element. |
| `focus(selector)` | `selector (str)` | Sets the browser's active focus to the specified element, triggering "onfocus" events. |

#### Usage Example:

```python
# Use standard 'type' for speed on non-sensitive fields
browser.type("#search", "isoAutomate documentation", timeout=5)

# Use 'press_keys' for fields that listen for keyup/keydown events (like passwords)
browser.press_keys("input[name='password']", "securePassword123")

# Clear a field before updating it
browser.clear("#email-field")
browser.type("#email-field", "support@isoautomate.com")

# Focus and submit
browser.focus("#login-btn")
browser.submit("#login-form")
```

### 4. GUI Actions (OS-Level Control)

GUI actions operate at the **hardware level**. Instead of sending Javascript events through the browser engine, they move a virtual mouse and press virtual keys on the remote machine's operating system. 

> **Note:** These actions require a `_profiled` browser engine (e.g., `chrome_profiled`).

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `gui_click_element(selector, timeframe)` | `selector (str)`, `timeframe (float=0.25)` | Physically moves the OS cursor to the element's coordinates and clicks. `timeframe` controls the speed of the cursor movement. |
| `gui_click_x_y(x, y, timeframe)` | `x, y (int)`, `timeframe (float=0.25)` | Physically clicks on raw pixel coordinates on the screen. |
| `gui_hover_element(selector)` | `selector (str)` | Physically moves the OS cursor to hover over an element. |
| `gui_drag_and_drop(drag, drop, timeframe)` | `drag, drop (str)`, `timeframe (float=0.35)` | Performs a hardware-level press, drag movement, and release gesture. |
| `gui_write(text)` | `text (str)` | Direct hardware-level keyboard input. This types into the element that currently has the OS focus. |
| `gui_press_keys(keys_list)` | `keys_list (list)` | Sends a list of specific hardware keys (e.g., `['control', 'c']`). |
| `gui_click_captcha()` | None | Automatically locates the verification checkbox in common captcha widgets and performs a physical OS-level click. |
| `solve_captcha()` | None | A high-level trigger that handles the OS-level movement required to check verification boxes. |

#### High-Fidelity Examples:

```python
# Perform a hardware-level click on a button to mimic real human interaction
browser.gui_click_element("#login-submit", timeframe=0.5)

# Drag a physical slider or element
browser.gui_drag_and_drop("#source-box", "#target-bin")

# Type using the OS virtual keyboard (useful for bypassing JS-level listeners)
browser.focus("#comment-box")
browser.gui_write("Typing at the hardware level.")

# Handle verification checkboxes automatically
browser.solve_captcha()
```

### 5. Selects & Dropdowns

These methods simplify interacting with standard HTML `<select>` elements and custom dropdown menus.

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `select_option_by_text(selector, text)` | `selector, text (str)` | Selects an option from a dropdown list based on the visible text shown to the user. |
| `select_option_by_value(selector, value)` | `selector, value (str)` | Selects an option based on its internal HTML `value` attribute. |
| `select_option_by_index(selector, index)` | `selector (str)`, `index (int)` | Selects an option based on its position in the list (starting from `0`). |

#### Examples:

```python
# Select "United States" from a country list by its visible name
browser.select_option_by_text("#country-select", "United States")

# Select an option where the HTML looks like <option value="USD">Dollar</option>
browser.select_option_by_value("#currency", "USD")

# Select the first option in a list
browser.select_option_by_index("#category", 0)
```

### 6. Window & Tab Management

These methods allow you to orchestrate multiple browser contexts, switch between tabs, and control the physical dimensions of the browser window.

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `open_new_tab(url)` | `url (str)` | Opens a new browser tab and navigates to the specified URL. Automatically switches focus to the new tab. |
| `open_new_window(url)` | `url (str)` | Opens a completely new browser window instance and navigates to the URL. |
| `switch_to_tab(index)` | `index (int=-1)` | Switches the active focus to a different tab. `0` is the first tab, `-1` is the most recently opened tab. |
| `switch_to_window(index)` | `index (int=-1)` | Switches focus to a different window instance. |
| `close_active_tab()` | None | Closes the current tab. Focus will automatically shift to the next available tab. |
| `maximize()` | None | Expands the browser window to fill the entire screen. |
| `minimize()` | None | Minimizes the browser window to the taskbar/dock. |
| `medimize()` | None | Resizes the window to a medium, standard size (Requires `_profiled` engine). |
| `tile_windows()` | None | Organizes all open windows into a grid pattern on the screen (Requires `_profiled` engine). |
| `set_window_size(w, h)` | `width (int)`, `height (int)` | Sets the browser viewport to a specific pixel resolution (e.g., 1280x720). |

#### Window Control Examples:

```python
# Open a second site in a new tab
browser.open_new_tab("https://google.com")

# Switch back to the original tab (first one)
browser.switch_to_tab(0)

# Set a specific resolution for responsive testing
browser.set_window_size(375, 812) # iPhone X dimensions
```

### 7. Data Extraction (Getters)

These methods allow you to retrieve data from the remote browser and return it to your local Python script for processing.

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `get_text(selector)` | `selector (str="body")` | Retrieves the visible text content of an element. Defaults to the entire page body. |
| `get_title()` | None | Returns the current page title as shown in the browser tab. |
| `get_current_url()` | None | Returns the absolute URL of the page currently being viewed. |
| `get_page_source()` | None | Returns the full raw HTML source code of the current page. |
| `get_html(selector)` | `selector (str=None)` | Returns the inner HTML of a specific element. If no selector is provided, returns the `<html>` content. |
| `get_attribute(selector, attr)` | `selector, attr (str)` | Retrieves the value of a specific HTML attribute (e.g., `src`, `href`, `value`). |
| `get_element_attributes(sel)` | `selector (str)` | Returns a dictionary containing all attributes of the targeted element. |
| `get_user_agent()` | None | Returns the User Agent string currently being used by the browser. |
| `get_cookie_string()` | None | Returns all cookies for the current domain formatted as a single string. |
| `get_element_rect(sel)` | `selector (str)` | Returns a dictionary with the element's position and size (`x`, `y`, `width`, `height`). |
| `get_window_rect()` | None | Returns the browser window's current dimensions and position. |
| `is_element_visible(sel)` | `selector (str)` | Returns `True` if the element is currently visible on the screen. |
| `is_text_visible(text)` | `text (str)` | Returns `True` if the specified text is visible anywhere on the page. |
| `get_performance_metrics()`| None | Returns detailed network and rendering performance metrics from the browser engine. |

#### Data Extraction Examples:

```python
# Get the price of a product
price = browser.get_text(".product-price")

# Extract a link from a button
download_url = browser.get_attribute("#download-link", "href")

# Check visibility before interacting
if browser.is_element_visible("#cookie-consent"):
    browser.click("#accept-all")
```

### 8. Cookies & Session Storage

These methods provide direct control over browser cookies and storage, allowing you to manage sessions, bypass logins, or clear tracking data manually.

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `get_cookies()` | None | Returns a list of dictionaries containing all cookies for the current domain. |
| `get_cookie_string()` | None | Returns all cookies formatted as a single string (useful for header injection). |
| `add_cookie(cookie_dict)` | `cookie_dict (dict)` | Injects a new cookie into the current session. |
| `delete_all_cookies()` | None | Clears all cookies from the current browser session. |
| `get_local_storage()` | None | Retrieves all key-value pairs stored in the browser's `localStorage`. |
| `get_session_storage()` | None | Retrieves all key-value pairs stored in the browser's `sessionStorage`. |
| `clear_local_storage()` | None | Deletes all data stored in `localStorage`. |
| `clear_session_storage()` | None | Deletes all data stored in `sessionStorage`. |

#### Usage Examples:

```python
# Save cookies to a file for later use
cookies = browser.get_cookies()

# Manually inject a session cookie to bypass login
browser.add_cookie({
    "name": "session_id",
    "value": "xyz123",
    "domain": "example.com"
})

# Clear all storage to start a clean session
browser.delete_all_cookies()
browser.clear_local_storage()
```

### 9. Wait & Verification (Assertions)

These methods are essential for handling dynamic content. They ensure your script waits for elements to load before interacting, preventing "Element Not Found" errors.

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `wait(seconds)` | `seconds (float)` | Performs a hard pause for the specified number of seconds. |
| `wait_for_element(selector, timeout)` | `selector (str)`, `timeout (int=None)` | Pauses execution until the element appears in the DOM. |
| `wait_for_text(text, timeout)` | `text (str)`, `timeout (int=None)` | Pauses execution until the specific text is visible on the page. |
| `assert_element(selector)` | `selector (str)` | Validates that an element exists. Raises an `AssertionError` if not found. |
| `assert_text(text, selector)` | `text (str)`, `selector (str="body")` | Validates that specific text exists within a chosen element (default: whole page). |
| `find_element(selector)` | `selector (str)` | Returns the metadata/coordinates for an element once it is located. |
| `find_elements(selector)` | `selector (str)` | Returns a list of all elements matching the selector. |

#### Usage Examples:

```python
# Wait for a slow-loading dashboard to appear
browser.wait_for_element("#dashboard-main", timeout=20)

# Verify that login was successful
browser.assert_text("Welcome back, User!", selector="h1")

# Hard pause (use sparingly)
browser.wait(2.5)
```

### 10. Scripting & Advanced Features

These methods allow you to extend the SDK's capabilities by executing custom logic directly within the browser context or retrieving advanced metadata.

| Method | Arguments | Description |
| :--- | :--- | :--- |
| `execute_script(script)` | `script (str)` | Executes raw Javascript within the current page context and returns the result. |
| `get_performance_metrics()` | None | Returns a detailed dictionary of Chrome performance logs (timing, memory, etc.). |
| `highlight(selector)` | `selector (str)` | Visually highlights an element in the browser (useful for debugging or video recordings). |
| `internalize_links()` | None | Rewrites `target="_blank"` links to open in the current tab to maintain session control. |
| `get_user_agent()` | None | Retrieves the current browser's User Agent string. |

#### Usage Examples:

```python
# Execute JS to get the value of a complex hidden variable
user_id = browser.execute_script("return window.appConfig.currentUserId;")

# Highlight an element before clicking it for a better video recording
browser.highlight("#buy-now-button")
browser.click("#buy-now-button")

# Check performance to ensure the page isn't lagging
metrics = browser.get_performance_metrics()
print(f"Page Load Time: {metrics.get('loadEventEnd')}ms")
```

### 11. Full Example: Social Media Automation

This example demonstrates a complete workflow: using persistence to stay logged in, performing high-fidelity GUI clicks to bypass detection, and extracting data.

```python
from isoautomate import BrowserClient
import time

# Use the context manager for automatic cleanup
with BrowserClient() as browser:
    # 1. Acquire a browser with a persistent profile for "User_Alpha"
    # We use 'chrome_profiled' to enable hardware-level GUI actions
    browser.acquire(
        browser_type="chrome_profiled", 
        profile="User_Alpha", 
        record=True
    )

    # 2. Navigate and wait for content
    browser.open_url("https://example-social-media.com/login")
    
    # 3. Handle login if not already logged in
    if browser.is_text_visible("Sign In"):
        browser.type("#username", "my_bot_user")
        browser.press_keys("#password", "secure_pass_123")
        
        # Use GUI click for the final submit to mimic human behavior
        browser.gui_click_element("#login-btn")
        
        # Wait for the dashboard to confirm successful login
        browser.wait_for_element(".dashboard-feed", timeout=15)

    # 4. Interact with the feed
    browser.hover(".first-post")
    browser.click(".like-button")
    
    # 5. Extract data to your local script
    post_stats = browser.get_text(".post-stats")
    print(f"Current Post Stats: {post_stats}")

    # 6. Session video is automatically saved on the server
    print(f"View execution recording at: {browser.video_url}")

# Connection is closed here, and the browser is released back to the fleet
```

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for more details.

## Contributing

We welcome contributions to the isoAutomate Python SDK! If you'd like to help improve the platform, please follow these steps:

1.  **Fork** the repository.
2.  **Create a new feature branch** (`git checkout -b feature/your-feature-name`).
3.  **Commit your changes** (`git commit -m 'Add some feature'`).
4.  **Push to the branch** (`git push origin feature/your-feature-name`).
5.  **Open a Pull Request**.

For major changes, please open an issue first to discuss what you would like to change.

---

<div align="center">
  <p>Built for the Sovereign Web. Powered by <b>isoAutomate</b>.</p>
  <a href="https://isoautomate.com">Official Website</a> • 
  <a href="https://isoautomate.com/docs">Full API Reference</a> • 
  <a href="mailto:support@isoautomate.com">Support</a>
</div>