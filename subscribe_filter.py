"""Builds the ffmpeg -vf filter string for a slide-up/hold/slide-down
SUBSCRIBE button appearing at each timestamp in TIMES."""

BOX_W = 340
BOX_H = 90
SLIDE_IN = 0.35
HOLD = 3.0
SLIDE_OUT = 0.45
DUR = SLIDE_IN + HOLD + SLIDE_OUT
BOTTOM_MARGIN = 90  # distance from bottom edge of frame to box bottom

FONT = "C\\:/Windows/Fonts/arialbd.ttf"


def progress_expr(t_var, T):
    """0->1 slide-in, hold at 1, 1->0 slide-out; 0 outside the window."""
    local = f"({t_var}-{T})"
    grow = f"({local}/{SLIDE_IN})"
    hold = "1.0"
    shrink = f"(({DUR}-{local})/{SLIDE_OUT})"
    return (
        f"if(lt({local},0),0,"
        f"if(lt({local},{SLIDE_IN}),{grow},"
        f"if(lt({local},{DUR}-{SLIDE_OUT}),{hold},"
        f"if(lt({local},{DUR}),{shrink},0))))"
    )


def build_filters(times):
    filters = []
    for T in times:
        p = progress_expr("t", T)
        enable = f"between(t,{T},{T + DUR})"

        # drawbox context: iw/ih
        box_y_hidden = "ih"
        box_y_shown = f"(ih-{BOTTOM_MARGIN}-{BOX_H})"
        box_y_expr = f"({box_y_hidden}-(({box_y_hidden})-({box_y_shown}))*({p}))"
        box_x_expr = f"(iw-{BOX_W})/2"

        filters.append(
            f"drawbox=x='{box_x_expr}':y='{box_y_expr}':w={BOX_W}:h={BOX_H}:"
            f"color=red@0.90:t=fill:enable='{enable}'"
        )

        # drawtext context: main_w/main_h (iw/ih are not valid here)
        text_y_hidden = "main_h"
        text_y_shown = f"(main_h-{BOTTOM_MARGIN}-{BOX_H})"
        text_y_expr = (
            f"({text_y_hidden}-(({text_y_hidden})-({text_y_shown}))*({p}))"
            f"+{BOX_H // 2 - 17}"
        )
        filters.append(
            "drawtext=fontfile='" + FONT + "':text='SUBSCRIBE':"
            f"fontsize=32:fontcolor=white:"
            f"x='(main_w-text_w)/2':y='{text_y_expr}':"
            f"enable='{enable}'"
        )
    return filters


if __name__ == "__main__":
    print(build_filters([180]))
