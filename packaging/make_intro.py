"""Render the one-page Chinese product introduction with an embedded font."""

import argparse
from html import escape
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from fontTools import subset
from fontTools.ttLib import TTFont as OpenTypeFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import registerFont, stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import release_info as release


BG = "#F4F5ED"
INK = "#26392D"
SUBTLE = "#4F6253"
GREEN = "#263A30"
PALE = "#E6EDDC"
EDGE = "#D2DBCC"
BRIGHT = "#F4F6EB"
SETTING_LINE = "各项监测可独立开关，提醒可选中央大窗或右下气泡。"
FIXED_TEXT = "派徕设计 Windows 桌面习惯助手 主要功能 安装与使用 制作人 官网 合作邮箱 版本发布 2026/11/20"


def embed_fonts(variable_font: Path, folder: Path) -> None:
    copy = " ".join((FIXED_TEXT, SETTING_LINE, release.APP_NAME, release.DESCRIPTION, release.COMPANY, release.SLOGAN,
                     *release.SERVICES, release.CREATORS, release.WEBSITE, release.COOPERATION_EMAIL,
                     release.SYSTEM_REQUIREMENTS, release.PRIVACY, *(" ".join(row) for row in release.FEATURES)))
    copy += "".join(chr(code) for code in range(32, 127)) + "，。、（）：·"
    for name, weight in (("NiuMaText", 400), ("NiuMaTitle", 700)):
        source = OpenTypeFont(variable_font)
        selector = subset.Subsetter()
        selector.populate(text=copy)
        selector.subset(source)
        static = instantiateVariableFont(source, {"wght": weight}, inplace=True, updateFontNames=True)
        path = folder / f"{name}.ttf"
        static.save(path)
        registerFont(TTFont(name, str(path)))


def render(destination: Path) -> None:
    page = Canvas(str(destination), pagesize=A4, pageCompression=1)
    page.setTitle(f"{release.APP_NAME} v{release.APP_VERSION} 产品介绍")
    page.setAuthor(release.COMPANY)
    page.setCreator("PLAY atelier")
    page.setSubject("Windows 桌面习惯助手")
    width, height = A4

    def fill(color):
        page.setFillColor(HexColor(color))

    def text(copy, x, y, size, color=INK, *, bold=False):
        fill(color)
        page.setFont("NiuMaTitle" if bold else "NiuMaText", size)
        page.drawString(x, y, copy)

    def paragraph(copy, x, top, available, *, size=10.5, color=SUBTLE, leading=17):
        style = ParagraphStyle("text", fontName="NiuMaText", fontSize=size, leading=leading,
                               textColor=HexColor(color), wordWrap="CJK", splitLongWords=True)
        block = Paragraph(escape(copy), style)
        _, needed = block.wrap(available, 200)
        block.drawOn(page, x, top - needed)

    def line(x1, y1, x2, y2, color=EDGE, stroke=.7):
        page.setStrokeColor(HexColor(color))
        page.setLineWidth(stroke)
        page.line(x1, y1, x2, y2)

    fill(BG)
    page.rect(0, 0, width, height, stroke=0, fill=1)
    page.drawImage(ImageReader(str(ROOT / "assets" / "studio-mark.png")), 43, 783,
                   width=47, height=21, mask="auto", preserveAspectRatio=True, anchor="w")
    text(release.BRAND, 102, 789, 10.5, bold=True)
    version = f"v{release.APP_VERSION}  ·  {release.RELEASE_DATE.replace('-', '/')}"
    text(version, 551 - stringWidth(version, "NiuMaText", 9), 788, 9, SUBTLE)

    fill(GREEN)
    page.roundRect(43, 556, 509, 202, 17, stroke=0, fill=1)
    for x in range(365, 535, 18):
        line(x, 572, x, 739, "#3A4E40", .45)
    for y in range(576, 745, 18):
        line(361, y, 535, y, "#3A4E40", .45)
    fill("#AEC08B")
    page.circle(446, 656, 72, fill=1, stroke=0)
    page.setStrokeColor(HexColor("#748F69"))
    page.setLineWidth(.8)
    page.circle(446, 656, 79, fill=0, stroke=1)
    with Image.open(ROOT / "assets" / "niuma-mascot.png") as art:
        sprite = art.convert("RGBA")
        page.drawImage(ImageReader(sprite), 380, 590, width=132, height=132, mask="auto")
    text("Windows 桌面习惯助手", 65, 718, 11, "#C4D8AF", bold=True)
    text(release.APP_NAME, 63, 661, 41, BRIGHT, bold=True)
    text("用本机摄像头识别桌前动作，帮你少啃手、记喝水。", 65, 611, 11, BRIGHT)
    text("忙久了也会提醒你起来动一动。", 65, 592, 11, BRIGHT)

    text("主要功能", 43, 520, 17, bold=True)
    for index, (heading, details) in enumerate(release.FEATURES):
        x = 43 + (index % 2) * 262
        y = 484 - (index // 2) * 65
        line(x, y + 22, x + 247, y + 22)
        text(heading, x, y, 12, bold=True)
        paragraph(details, x, y - 9, 234, size=10, leading=15)
    paragraph(SETTING_LINE, 43, 362, 504, size=9.5)

    fill(PALE)
    page.roundRect(43, 264, 509, 65, 10, stroke=0, fill=1)
    text("安装与使用", 56, 308, 11, bold=True)
    text(release.SYSTEM_REQUIREMENTS, 56, 291, 9.5, SUBTLE)
    text(release.PRIVACY, 56, 276, 9.5, SUBTLE)

    line(43, 231, 552, 231)
    text(release.COMPANY, 43, 204, 15, bold=True)
    text(release.SLOGAN, 43, 180, 11, SUBTLE)
    paragraph("、".join(release.SERVICES), 43, 163, 246, size=10, leading=16)
    line(306, 111, 306, 210)

    def contact(heading, value, y, url=None):
        text(heading, 322, y, 9, SUBTLE)
        text(value, 389, y, 10.5, INK, bold=heading == "制作人")
        if url:
            length = stringWidth(value, "NiuMaText", 10.5)
            line(389, y - 2, 389 + length, y - 2, "#7C9380", .5)
            page.linkURL(url, (388, y - 4, 392 + length, y + 12), relative=0, thickness=0)

    contact("制作人", release.CREATORS, 202)
    contact("官网", release.WEBSITE, 173, release.WEBSITE_URL)
    contact("合作邮箱", release.COOPERATION_EMAIL, 144, f"mailto:{release.COOPERATION_EMAIL}")
    line(43, 89, 552, 89)
    text(f"{release.APP_NAME}  v{release.APP_VERSION}", 43, 67, 8.5, SUBTLE)
    page.showPage()
    page.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("variable_font", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as scratch:
        embed_fonts(args.variable_font, Path(scratch))
        render(args.output)
