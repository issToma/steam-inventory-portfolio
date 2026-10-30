from invinv.ui.common import rarity_color, sparkline_points
from invinv.ui.layout import themed_chart


def test_themed_chart_colours_axes_legend_and_tooltip() -> None:
    options = themed_chart(
        {
            "legend": {"top": 0},
            "tooltip": {"trigger": "axis"},
            "xAxis": [{"type": "time"}, {"type": "time"}],
            "yAxis": {"type": "value", "axisLabel": {":formatter": "v => v"}},
        }
    )

    assert options["textStyle"]["color"]
    assert all(axis["axisLabel"]["color"] for axis in options["xAxis"])
    assert options["yAxis"]["axisLabel"][":formatter"] == "v => v"
    assert options["legend"]["top"] == 0
    assert options["tooltip"]["backgroundColor"]


def test_rarity_colour_per_game() -> None:
    assert rarity_color("Covert", None, None, 730) == "#eb4b4b"
    assert rarity_color("Covert", "★", None, 730) == "#8650ac"
    assert rarity_color(None, None, "#4b69ff", appid=570) == "#4b69ff"
    assert rarity_color("Covert", None, None, appid=570) is None


def test_sparkline_points() -> None:
    assert sparkline_points([1.0]) == ""
    assert sparkline_points([1.0, 2.0], width=10, height=10) == "0.0,9.0 10.0,1.0"


def test_nav_key_of_a_path() -> None:
    from invinv.ui.shell import nav_key

    assert nav_key("/") == "portfolio"
    assert nav_key("/market?x=1") == "market"
    assert nav_key("/item/5") == ""
