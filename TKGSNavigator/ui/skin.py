"""Build a resolution-scaled skin so the screen fits HD and SD desktops."""

from enigma import getDesktop

BUTTONS = (
    ("red", 28, "#943d48"),
    ("green", 244, "#28715c"),
    ("yellow", 460, "#80652d"),
    ("blue", 676, "#335e95"),
)


def make_skin():
    size = getDesktop(0).size()
    scale = min((size.width() - 32) / 900.0, (size.height() - 32) / 620.0, 1.4)
    scale = max(0.5, scale)

    def rect(x, y, w, h):
        return 'position="%d,%d" size="%d,%d"' % tuple(int(v * scale) for v in (x, y, w, h))

    def label(name, x, y, w, h, font=22, color="#e9eff6", background="#101b2b"):
        return (
            '<widget name="%s" %s font="Regular;%d" foregroundColor="%s" '
            'backgroundColor="%s" valign="center" />'
            % (name, rect(x, y, w, h), int(font * scale), color, background)
        )

    widgets = [
        label("title", 28, 16, 844, 42, 30, "#57d4bd"),
        label("subtitle", 28, 62, 844, 28, 19),
        '<widget name="config" %s itemHeight="%d" font="Regular;%d" />'
        % (rect(28, 102, 844, 180), int(30 * scale), int(21 * scale)),
        label("status", 28, 294, 844, 48, 21),
        '<widget name="progress" %s borderWidth="1" foregroundColor="#57d4bd" '
        'backgroundColor="#203149" />' % rect(28, 352, 844, 10),
        label("metrics", 28, 370, 844, 28, 19, "#a8bbd0"),
        '<widget name="channels" %s itemHeight="%d" font="Regular;%d" '
        'scrollbarMode="showOnDemand" />'
        % (rect(28, 406, 844, 142), int(28 * scale), int(20 * scale)),
    ]
    for name, x, color in BUTTONS:
        widgets.append(label(name, x, 568, 196, 32, 19, background=color))
    return (
        '<screen name="NavigatorScreen" position="center,center" size="%d,%d" '
        'title="TKGS Navigator" backgroundColor="#101b2b">%s</screen>'
        % (int(900 * scale), int(620 * scale), "".join(widgets))
    )
