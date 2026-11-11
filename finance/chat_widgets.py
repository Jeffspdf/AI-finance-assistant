"""Rounded surfaces for the conversation UI, with keyboard and pointer controls."""

import tkinter as tk
from tkinter import font as tkfont, ttk

from PIL import Image, ImageDraw, ImageTk

from .ui_scale import point_font, px
from .ui_theme import BORDER, INK, MUTED, PRIMARY


CHAT_INPUT = '#f6f8f7'
SPACE_FILL = '#f0f5f2'
BUTTON_LOOKS = {
    'action': ('#eff5f1', '#e4efe9', '#dcece3', PRIMARY, 10, (11, 6), None, 'center'),
    'card': ('#f6f8f7', '#edf5f0', '#e1efe7', INK, 15, (14, 11), BORDER, 'w'),
    'nav': ('white', '#eff5f1', '#dcece3', MUTED, 10, (12, 3), None, 'w'),
    'new': ('#eff6f2', '#e7f3ec', '#dcece3', PRIMARY, 12, (13, 7), None, 'w'),
}


def _surface(size, radius, fill, outline=None, *, arrow=False):
    scale = 3
    width, height = (value * scale for value in size)
    image = Image.new('RGBA', (width, height))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width-1, height-1), radius=radius*scale,
                           fill=fill, outline=outline, width=scale)
    if arrow:
        x, y = width // 2, height // 2
        half = px(7) * scale
        thickness = px(2) * scale
        draw.line((x, y+half, x, y-half), fill='white', width=thickness)
        draw.line((x-half, y, x, y-half, x+half, y), fill='white', width=thickness, joint='curve')
    return image.resize(size, Image.Resampling.LANCZOS)


def install_chat_styles(root):
    if hasattr(root, '_chat_style_images'):
        return
    style = ttk.Style(root)
    images = root._chat_style_images = []

    def picture(size, radius, fill, outline=None, arrow=False):
        photo = ImageTk.PhotoImage(_surface(size, radius, fill, outline, arrow=arrow), master=root)
        images.append(photo)
        return photo

    size = (px(42),) * 2
    radius = size[0] // 2
    style.element_create('ChatSend.surface', 'image', picture(size, radius, PRIMARY, arrow=True),
                         ('pressed', picture(size, radius, '#104b45', arrow=True)),
                         ('focus', picture(size, radius, PRIMARY, '#80bbb0', arrow=True)),
                         ('active', picture(size, radius, '#115e59', arrow=True)), sticky='')
    style.layout('ChatSend.TButton', [('ChatSend.surface', {'sticky': ''})])

    style.configure('ChatSpace.TCombobox', fieldbackground=SPACE_FILL, background=SPACE_FILL, borderwidth=0,
                    bordercolor=SPACE_FILL, lightcolor=SPACE_FILL, darkcolor=SPACE_FILL, padding=px((4, 4)))
    style.map('ChatSpace.TCombobox', fieldbackground=[('readonly', SPACE_FILL)],
              background=[('readonly', SPACE_FILL)], bordercolor=[('focus', SPACE_FILL)])


class RoundedButton(tk.Canvas):
    def __init__(self, parent, *, text, command, look='action'):
        fill, hover, pressed, color, radius, padding, outline, anchor = BUTTON_LOOKS[look]
        font = tkfont.Font(root=parent, font=point_font(10))
        lines = text.splitlines()
        super().__init__(parent, width=max(font.measure(line) for line in lines) + 2*px(padding[0]),
                         height=font.metrics('linespace')*len(lines) + 2*px(padding[1]),
                         bg=parent.cget('bg'), borderwidth=0, highlightthickness=0, takefocus=True,
                         cursor='hand2')
        self.text, self.command = text, command
        self.fill, self.hover_fill, self.pressed_fill = fill, hover, pressed
        self.color, self.outline, self.anchor = color, outline, anchor
        self.radius, self.inset = px(radius), px(padding[0])
        self.hovered = self.pressed = self.selected = self.focused = False
        self.picture = None
        self.last_paint = None
        self.surface = self.create_image(0, 0, anchor='nw')
        self.label = self.create_text(0, 0, text=text, fill=color, font=point_font(10), anchor=anchor)
        self.bind('<Configure>', lambda _: self._paint())
        self.bind('<Enter>', lambda _: self._hover(True))
        self.bind('<Leave>', lambda _: self._hover(False))
        self.bind('<FocusIn>', lambda _: self._focus(True))
        self.bind('<FocusOut>', lambda _: self._focus(False))
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<space>', self._activate)
        self.bind('<Return>', self._activate)
        self.bind('<Destroy>', lambda _: setattr(self, 'picture', None))

    def invoke(self):
        return self.command()

    def cget(self, key):
        if key == 'text':
            return self.text
        return super().cget(key)

    __getitem__ = cget

    def set_selected(self, selected):
        self.selected = selected
        self._paint()

    def _hover(self, active):
        self.hovered = active
        self._paint()

    def _focus(self, focused):
        self.focused = focused
        self._paint()

    def _press(self, _event):
        self.focus_set()
        self.pressed = True
        self._paint()

    def _release(self, event):
        armed, self.pressed = self.pressed, False
        self._paint()
        if armed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def _activate(self, _event):
        self.invoke()
        return 'break'

    def _paint(self):
        width, height = self.winfo_width(), self.winfo_height()
        if min(width, height) <= 1:
            return
        if self.pressed and self.hovered:
            fill = self.pressed_fill
        elif self.hovered:
            fill = self.hover_fill
        elif self.selected:
            fill = '#e7f3ec'
        else:
            fill = self.fill
        outline = '#8cbdb1' if self.focused else self.outline
        key = (width, height, fill, outline, self.selected)
        if key == self.last_paint:
            return
        self.last_paint = key
        self.picture = ImageTk.PhotoImage(_surface((width, height), self.radius, fill, outline), master=self)
        self.itemconfigure(self.surface, image=self.picture)
        self.coords(self.label, self.inset if self.anchor == 'w' else width / 2, height / 2)
        self.itemconfigure(self.label, fill=PRIMARY if self.selected else self.color,
                           width=max(1, width - 2*self.inset))


class RoundedPanel(tk.Frame):
    """Draw a resizeable rounded shell behind an ordinary content frame."""

    def __init__(self, parent, *, fill, radius, inset, outline=None):
        super().__init__(parent, bg=parent.cget('bg'))
        self.fill = fill
        self.radius = px(radius)
        self.outline = outline
        self.picture = None
        self.size = None
        self.canvas = tk.Canvas(self, bg=self.cget('bg'), highlightthickness=0, borderwidth=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.surface = self.canvas.create_image(0, 0, anchor='nw')
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill='both', expand=True, padx=px(inset[0]), pady=px(inset[1]))
        self.bind('<Configure>', self._paint)
        self.bind('<Destroy>', lambda _: setattr(self, 'picture', None))

    def _paint(self, event):
        size = (event.width, event.height)
        if size == self.size:
            return
        self.size = size
        self.picture = ImageTk.PhotoImage(_surface(size, self.radius, self.fill, self.outline), master=self)
        self.canvas.itemconfigure(self.surface, image=self.picture)
