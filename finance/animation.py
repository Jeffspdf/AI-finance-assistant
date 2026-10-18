"""Four-state hand-drawn sprite player; all Tk work stays on the UI thread."""
import tkinter as tk
from PIL import Image, ImageTk

STATES = ('idle','wave','thinking','success')
TIMELINES = {'idle': [(0,2100),(1,100),(2,120),(3,150)],
             'wave': [(0,160),(1,160),(2,160),(3,160)]*3,
             'thinking': [(0,380),(1,380),(2,380),(3,380)],
             'success': [(0,240),(1,240),(2,400),(3,300)]*2}


def load_frames(path, size):
    with Image.open(path) as sheet:
        sheet=sheet.convert('RGBA')
        w,h=sheet.size
        return {state:[sheet.crop((col*w//4,row*h//4,(col+1)*w//4,(row+1)*h//4)).resize(size,Image.Resampling.LANCZOS)
                       for col in range(4)] for row,state in enumerate(STATES)}


def opaque_frame(image,background):
    # Tk's color-key window uses a separate compositor. Always repaint every
    # pixel, including the old pose's silhouette, rather than blend over it.
    canvas=Image.new('RGB',image.size,background)
    canvas.paste(image,mask=image.getchannel('A'))
    return canvas


class SpritePlayer:
    def __init__(self,label,path,size):
        self.label=label
        self.frames={k:[ImageTk.PhotoImage(opaque_frame(im,label.cget('bg')),master=label) for im in v] for k,v in load_frames(path,size).items()}
        self.state='idle';self.index=0;self.job=None;self.paused=False;self.closed=False
        label.bind('<Destroy>',self._destroy,add='+')
        self.play('idle')

    def _destroy(self,event):
        if event.widget is self.label:
            self.closed=True
            self.cancel()

    def cancel(self):
        if self.job is not None:
            self.label.after_cancel(self.job);self.job=None

    def play(self,state):
        if self.closed: return
        if state not in STATES: state='idle'
        self.cancel();self.state=state;self.index=0
        self.tick()

    def tick(self):
        self.job=None
        if self.closed: return
        timeline=TIMELINES[self.state]
        frame,delay=timeline[self.index]
        self.label.configure(image=self.frames[self.state][frame])
        if self.paused:return
        self.index+=1
        if self.index>=len(timeline):
            self.index=0
            if self.state in ('wave','success'):self.state='idle'
        self.job=self.label.after(delay,self.tick)

    def pause(self,paused):
        self.paused=paused
        if paused:self.cancel()
        else:self.play('idle')
