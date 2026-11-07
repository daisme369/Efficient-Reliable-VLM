"""Create the purely synthetic, display-only chest radiograph fixtures."""

from pathlib import Path
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


WIDTH = 800
HEIGHT = 880
X, Y = np.meshgrid(np.arange(WIDTH), np.arange(HEIGHT))
OUT = Path(__file__).resolve().parent


def ellipse(cx, cy, rx, ry, slope=0):
    return np.exp(-1.75 * (((X - cx - slope * (Y - cy)) / rx) ** 2 + ((Y - cy) / ry) ** 2))


def shape(points, blur):
    layer = Image.new("L", (WIDTH, HEIGHT))
    ImageDraw.Draw(layer).polygon(points, fill=255)
    return np.asarray(layer.filter(ImageFilter.GaussianBlur(blur)), dtype=float) / 255


def line(layer, points, value, width):
    ImageDraw.Draw(layer).line(points, fill=value, width=width, joint="curve")


def curve(points, count=100):
    anchors = np.array(points, dtype=float)
    locations = np.linspace(0, 1, count)
    return [
        tuple(
            ((1-t)**3 * anchors[0] + 3*t*(1-t)**2 * anchors[1]
             + 3*t*t*(1-t) * anchors[2] + t**3 * anchors[3]).tolist()
        )
        for t in locations
    ]


def paint(view, seed):
    rng = random.Random(seed)
    noise = np.random.default_rng(seed)
    body = shape([(54,174),(196,132),(304,96),(324,3),(476,3),(498,91),(610,129),
                  (755,173),(788,282),(738,320),(732,784),(708,879),(97,879),
                  (68,782),(71,320),(23,284)], 18)
    ribs = Image.new("L", (WIDTH, HEIGHT))
    vessels = Image.new("L", (WIDTH, HEIGHT))
    if view == "lat":
        body = shape([(236,0),(448,0),(461,77),(548,132),(632,222),(644,424),
                      (658,642),(707,879),(130,879),(139,687),(172,574),
                      (156,383),(143,221),(185,126)], 25)
        lung = shape([(219,255),(297,161),(432,169),(515,253),(555,432),(581,649),
                      (526,737),(314,752),(212,651),(180,462)], 30)
        chest = 8 + body * (67 + 73*ellipse(393,581,230,435)) - 78*lung
        chest += 66 * ellipse(445,575,139,188,0.22)
        for i in range(10):
            y = 179 + i*56
            line(ribs, curve([(475,y),(410,y+21),(366,y+43),(234,y+109)]), 56, 8)
            line(ribs, curve([(486,y+21),(413,y+55),(360,y+61),(241,y+140)]), 32, 9)
        line(ribs, curve([(492,97),(483,304),(489,520),(542,838)]), 80, 34)
        for i in range(15):
            y = 107 + i*47
            x = 492 + max(0, y-420)*.12
            ImageDraw.Draw(ribs).rounded_rectangle((x-25,y,x+25,y+34), radius=7, fill=90)
    else:
        left = shape([(188,222),(237,154),(295,145),(337,174),(367,266),(373,419),
                      (356,534),(320,655),(271,717),(142,725),(111,690),
                      (107,576),(124,432),(145,311)], 20)
        right = shape([(608,226),(562,158),(506,145),(463,175),(430,271),(421,421),
                       (449,515),(493,657),(539,713),(653,738),(695,701),
                       (698,567),(679,422),(655,316)], 20)
        chest = 7 + body * (43 + 65*ellipse(400,531,364,514)) - 52*(left + right)
        chest += 83*ellipse(449,561,174,228,.19) + 45*ellipse(402,263,56,283)
        chest -= 37*ellipse(403,185,17,150,-.04)
        chest += 36*ellipse(384,778,296,148) + 22*ellipse(571,785,149,124)
        if view == "ap":
            chest += 29*ellipse(553,600,88,102,-.3) + 19*ellipse(577,650,130,44)
        for sign in (-1, 1):
            def px(x):
                return 400 + sign*x

            for i in range(10):
                y = 187 + i*47
                spread = 164 + 9 * min(i, 8)
                value = 54 - i*2
                line(ribs, curve([(px(24),y+5),(px(104),y-67),
                                  (px(220),y-45),(px(294),y+46)]), value, 8)
                line(ribs, curve([(px(295),y+44),(px(286),y+114),
                                  (px(spread),y+133),(px(86),y+130)]), value-19, 9)
            line(ribs, curve([(px(33),215),(px(116),175),(px(189),115),(px(295),165)]), 81, 14)
            line(ribs, curve([(px(291),169),(px(296),253),(px(281),320),(px(241),384)]), 55, 13)
            line(ribs, curve([(px(311),178),(px(344),199),(px(346),258),(px(332),317)]), 69, 22)
            line(ribs, curve([(px(44),694),(px(129),616),(px(224),657),(px(288),713)]), 35, 10)
            for i in range(45):
                x0, y0 = px(rng.randrange(28,100)), rng.randrange(325,510)
                x1, y1 = px(rng.randrange(104,260)), rng.randrange(230,680)
                line(vessels, curve([(x0,y0),(x0+(x1-x0)*.2,y0+28),
                                     (x1-(x1-x0)*.22,y1-12),(x1,y1)]),
                     rng.randrange(14,49), rng.randrange(1,5))
        for i in range(16):
            y = 119 + i*45
            ImageDraw.Draw(ribs).rounded_rectangle((374,y,422,y+33),radius=8,fill=34)

    chest += np.asarray(ribs.filter(ImageFilter.GaussianBlur(5)), dtype=float)*.97
    chest += np.asarray(ribs.filter(ImageFilter.GaussianBlur(15)), dtype=float)*.46
    chest += np.asarray(vessels.filter(ImageFilter.GaussianBlur(1.8)), dtype=float)*.6
    chest += (noise.normal(0, 3, chest.shape) + noise.normal(0, .8, chest.shape))*body
    chest *= 0.99 + .06*ellipse(401,382,413,493)
    image = Image.fromarray(np.clip(chest, 4, 239).astype(np.uint8), mode="L")
    image.save(OUT / f"{view}.png", optimize=True)


if __name__ == "__main__":
    for asset, seed in [("pa", 81), ("ap", 155), ("lat", 311)]:
        paint(asset, seed)
