"""Four-state hand-drawn sprite player; all Tk work stays on the UI thread."""
import tkinter as tk
from PIL import Image, ImageTk

STATES = ('idle','wave','thinking','success')
TIMELINES = {'idle': [(0,2100),(1,100),(2,120),(3,150)],
             'wave': [(0,160),(1,160),(2,160),(3,160)]*3,
             'thinking': [(0,380),(1,380),(2,380),(3,380)],
             'success': [(0,240),(1,240),(2,400),(3,300)]*2}

# The generated atlas has uneven horizontal spacing. These measured bounds
# isolate complete poses; equal quarter-width slices cut hands into neighbours.
POSE_BOUNDS = (
    ((136,4,372,256),(470,3,710,256),(808,3,1049,256),(1147,3,1387,256)),
    ((127,0,407,256),(468,0,717,256),(802,0,1119,256),(1144,0,1459,256)),
    ((132,0,384,256),(470,0,733,256),(810,0,1063,256),(1146,0,1394,256)),
    ((128,0,419,256),(470,0,751,256),(806,0,1072,256),(1144,0,1425,256)),
)
HEAD_ANCHORS = (256,596,936,1276)


def load_frames(path, size):
    with Image.open(path) as sheet:
        sheet=sheet.convert('RGBA')
        if sheet.size != (1536,1024): raise ValueError('动画图集尺寸与逐帧裁切配置不匹配')
        result={}
        for row,state in enumerate(STATES):
            result[state]=[]
            for col,(left,top,right,bottom) in enumerate(POSE_BOUNDS[row]):
                pose=sheet.crop((left,row*256+top,right,row*256+bottom))
                canvas=Image.new('RGBA',(384,256),(0,0,0,0))
                canvas.paste(pose,(left-HEAD_ANCHORS[col]+192,top))
                result[state].append(canvas.resize(size,Image.Resampling.LANCZOS))
        return result


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
