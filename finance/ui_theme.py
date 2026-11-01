"""Flat desktop styles and a small Canvas meter. No extra UI dependency."""
import tkinter as tk
from tkinter import ttk
BG='#f7f8f7';INK='#1b2a26';MUTED='#6c7a75';PRIMARY='#0f766e';DANGER='#dc2626';BORDER='#e6ece9'

def apply_theme(root):
    style=ttk.Style(root);style.theme_use('clam')
    style.configure('.',font=('Microsoft YaHei UI',-13),background=BG,foreground=INK)
    style.configure('TEntry',padding=7,fieldbackground='white',bordercolor=BORDER,lightcolor=BORDER,darkcolor=BORDER,relief='flat')
    style.configure('TCombobox',padding=7,fieldbackground='white',background='white',bordercolor=BORDER,lightcolor=BORDER,darkcolor=BORDER,arrowcolor=PRIMARY,arrowsize=12,relief='flat')
    style.map('TCombobox',fieldbackground=[('readonly','white')],background=[('readonly','white')],foreground=[('readonly',INK)],bordercolor=[('focus',PRIMARY)])
    style.configure('Treeview',rowheight=38,background='white',fieldbackground='white',foreground=INK,borderwidth=0,relief='flat')
    style.configure('Treeview.Heading',background='#f1f5f3',foreground=MUTED,relief='flat',padding=(10,9),font=('Microsoft YaHei UI',-12))
    style.map('Treeview.Heading',background=[('active','#e6f4f1')],relief=[('active','flat')])
    style.map('Treeview',background=[('selected','#d9eeea')],foreground=[('selected',INK)])
    style.configure('Vertical.TScrollbar',background='#cbd8d2',troughcolor=BG,bordercolor=BG,arrowcolor=MUTED,lightcolor=BG,darkcolor=BG,relief='flat',arrowsize=10,width=10)
    return style

class Meter(tk.Canvas):
    def __init__(self,parent,maximum=100,value=0,length=150,**kwargs):
        kwargs.pop('mode',None)
        self.maximum=maximum;self.value=value;self.danger=kwargs.pop('danger',False)
        super().__init__(parent,height=12,width=length,highlightthickness=0,bg=parent.cget('bg'),**kwargs)
        self.bind('<Configure>',lambda _:self.redraw());self.redraw()
    def __setitem__(self,key,value):
        if key=='value':self.value=float(value);self.redraw()
        elif key=='maximum':self.maximum=float(value);self.redraw()
        else:super().__setitem__(key,value)
    def __getitem__(self,key):
        if key=='value':return self.value
        return super().__getitem__(key)
    def redraw(self):
        self.delete('all');width=max(self.winfo_width(),int(self.cget('width')))-12
        self.create_line(6,6,width+6,6,width=8,fill='#e6ece9',capstyle='round')
        ratio=max(0,min(1,self.value/self.maximum if self.maximum else 0))
        if ratio:self.create_line(6,6,6+width*ratio,6,width=8,fill=DANGER if self.danger else PRIMARY,capstyle='round')

def enable_dpi_awareness():
    import sys
    if sys.platform=='win32':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError,AttributeError):pass
