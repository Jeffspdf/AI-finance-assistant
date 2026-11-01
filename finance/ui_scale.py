"""96-DPI layout units; Tk positive point fonts scale independently."""
import math
import os

S = 1.0


def init_scale(root, scale=None):
    global S
    override = scale if scale is not None else os.environ.get('FINANCE_UI_SCALE')
    S = float(override) if override is not None else root.winfo_fpixels('1i') / 96.0
    if override is None and os.name == 'nt':
        import ctypes
        try:
            # Tk derives DPI from rounded screen millimetres (e.g. 143.858).
            # Windows provides the exact system value (144 for 150%).
            dpi=ctypes.windll.user32.GetDpiForSystem()
            if dpi:S=dpi/96.0
        except AttributeError:
            pass
    if not math.isfinite(S) or not .75 <= S <= 3:
        raise ValueError('UI scale must be between 0.75 and 3')
    if override is not None:
        root.tk.call('tk', 'scaling', 96 * S / 72)
    root.ui_scale = S
    return S


def px(value):
    if isinstance(value, (tuple, list)):
        return tuple(px(item) for item in value)
    return round(float(value) * S)


def logical(value):
    return float(value) / S


def point_font(size=10, bold=False, family='Microsoft YaHei UI'):
    return family, max(10, int(round(size))), 'bold' if bold else 'normal'


def work_area(root):
    if os.name == 'nt':
        import ctypes
        from ctypes.wintypes import RECT
        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def fitted_size(width, height, scale, area, margin=24):
    available_w = max(1, area[2] - area[0] - round(margin * scale))
    available_h = max(1, area[3] - area[1] - round(margin * scale))
    return min(round(width * scale), available_w), min(round(height * scale), available_h)


def set_window_geometry(window, width, height, minimum=None, area=None):
    area = area or work_area(window)
    w, h = fitted_size(width, height, S, area)
    # geometry describes client area: reserve room for the native title bar too.
    h = min(h, max(1, area[3] - area[1] - px(48)))
    x = area[0] + max(0, (area[2] - area[0] - w) // 2)
    y = area[1] + max(0, (area[3] - area[1] - h - px(48)) // 2)
    window.geometry(f'{w}x{h}+{x}+{y}')
    if minimum:
        window.minsize(min(px(minimum[0]), w), min(px(minimum[1]), h))
    return w, h


def fit_main_window(root):
    return set_window_geometry(root, 1200, 800, minimum=(1100, 720))
