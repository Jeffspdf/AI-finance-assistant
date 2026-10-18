from PIL import Image
from finance.animation import opaque_frame

def test_transparent_pixels_clear_previous_silhouette():
    frame=Image.new('RGBA',(4,4),(255,0,0,0))
    frame.putpixel((1,1),(0,100,0,255))
    result=opaque_frame(frame,'#fff0fe')
    assert result.mode=='RGB'
    assert result.getpixel((0,0))==(255,240,254)
    assert result.getpixel((1,1))==(0,100,0)
