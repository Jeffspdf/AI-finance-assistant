"""Flat desktop styles and a small Canvas meter. No extra UI dependency."""
import tkinter as tk
from tkinter import ttk
from .ui_scale import px, point_font
BG='#f7f8f7';INK='#1b2a26';MUTED='#6c7a75';PRIMARY='#0f766e';DANGER='#dc2626';BORDER='#e6ece9'

def apply_theme(root):
    style=ttk.Style(root);style.theme_use('clam')
    style.configure('.',font=point_font(),background=BG,foreground=INK)
    style.configure('TEntry',padding=px(7),fieldbackground='white',bordercolor=BORDER,lightcolor=BORDER,darkcolor=BORDER,relief='flat')
    style.configure('TCombobox',padding=px(7),fieldbackground='white',background='white',bordercolor=BORDER,lightcolor=BORDER,darkcolor=BORDER,arrowcolor=PRIMARY,arrowsize=px(12),relief='flat')
    style.map('TCombobox',fieldbackground=[('readonly','white')],background=[('readonly','white')],foreground=[('readonly',INK)],bordercolor=[('focus',PRIMARY)])
    style.configure('Treeview',rowheight=px(44),background='white',fieldbackground='white',foreground=INK,borderwidth=0,relief='flat')
    style.configure('Treeview.Heading',background='#f1f5f3',foreground=MUTED,relief='flat',padding=px((10,9)),font=point_font())
    style.map('Treeview.Heading',background=[('active','#e6f4f1')],relief=[('active','flat')])
    style.map('Treeview',background=[('selected','#d9eeea')],foreground=[('selected',INK)])
    style.configure('Vertical.TScrollbar',background='#cbd8d2',troughcolor=BG,bordercolor=BG,arrowcolor=MUTED,lightcolor=BG,darkcolor=BG,relief='flat',arrowsize=px(10),width=px(12))
    style.configure('TNotebook.Tab',padding=px((12,8)))
    return style

class Meter(tk.Canvas):
    def __init__(self,parent,maximum=100,value=0,length=150,**kwargs):
        kwargs.pop('mode',None)
        self.maximum=maximum;self.value=value;self.danger=kwargs.pop('danger',False)
        height=kwargs.pop('height',12)
        super().__init__(parent,height=px(height),width=px(length),highlightthickness=0,bg=parent.cget('bg'),**kwargs)
        self.bind('<Configure>',lambda _:self.redraw());self.redraw()
    def __setitem__(self,key,value):
        if key=='value':self.value=float(value);self.redraw()
        elif key=='maximum':self.maximum=float(value);self.redraw()
        else:super().__setitem__(key,value)
    def __getitem__(self,key):
        if key=='value':return self.value
        return super().__getitem__(key)
    def redraw(self):
        self.delete('all');width=max(self.winfo_width(),int(self.cget('width')))-px(12)
        self.create_line(px(6),px(6),width+px(6),px(6),width=px(8),fill='#e6ece9',capstyle='round')
        ratio=max(0,min(1,self.value/self.maximum if self.maximum else 0))
        if ratio:self.create_line(px(6),px(6),px(6)+width*ratio,px(6),width=px(8),fill=DANGER if self.danger else PRIMARY,capstyle='round')

def enable_dpi_awareness():
    import sys
    if sys.platform=='win32':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError,AttributeError):pass


def scrollable_form(window,padx=24,pady=18):
    """A form can exceed the available desktop height; keep its footer outside."""
    container=tk.Frame(window,bg=BG);container.pack(fill='both',expand=True)
    canvas=tk.Canvas(container,bg=BG,highlightthickness=0)
    scrollbar=ttk.Scrollbar(container,orient='vertical',command=canvas.yview)
    scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    body=tk.Frame(canvas,bg=BG,padx=px(padx),pady=px(pady))
    item=canvas.create_window(0,0,anchor='nw',window=body)
    body.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>',lambda event:canvas.itemconfigure(item,width=event.width))
    window.bind('<MouseWheel>',lambda event:canvas.yview_scroll(-int(event.delta/120),'units'),add='+')
    window.form_canvas=canvas
    return body
