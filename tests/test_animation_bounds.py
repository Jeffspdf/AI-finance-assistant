from pathlib import Path
from finance.animation import load_frames

def test_no_neighbouring_pose_at_edges():
    frames=load_frames(Path(__file__).resolve().parents[1]/'assets/campus_animation.png',(384,256))
    for state,images in frames.items():
        for index,image in enumerate(images):
            assert image.getchannel('A').crop((0,0,8,256)).getbbox() is None,(state,index,'left')
            assert image.getchannel('A').crop((380,0,384,256)).getbbox() is None,(state,index,'right')
    # First wave hand extends past the old x=384 split; it must be retained.
    assert frames['wave'][0].getchannel('A').crop((320,0,350,256)).getbbox() is not None
