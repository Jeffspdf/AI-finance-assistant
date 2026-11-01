import pytest
from finance import ui_scale


@pytest.mark.parametrize('scale,expected',[(1,(1200,800)),(1.25,(1500,1000)),(1.5,(1800,1200)),(2,(2400,1392))])
def test_size_scales_and_caps_to_work_area(scale,expected):
    assert ui_scale.fitted_size(1200,800,scale,(0,0,2560,1440)) == expected


def test_pixels_and_fonts_use_different_units(monkeypatch):
    monkeypatch.setattr(ui_scale,'S',1.5)
    assert ui_scale.px(44)==66
    assert ui_scale.px((10,8))==(15,12)
    assert ui_scale.logical(1800)==1200
    assert ui_scale.point_font(9)==('Microsoft YaHei UI',10,'normal')
