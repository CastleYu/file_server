"""Offscreen Qt capture for reverse UI acceptance."""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import QSize, Qt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts" / "ui_review"
OUT.mkdir(parents=True, exist_ok=True)

from abyssfs.protocol.base import FileServerProtocol, TlsConfig, SshConfig
from abyssfs.ui.constants import UISize
from abyssfs.ui.settings_dialog import SettingsDialog
from abyssfs.ui.protocol_dialog import ProtocolDialog
from abyssfs.ui.user_dialog import UserDialog, VirtualDirectoryDialog
from abyssfs.ui.main_window import _PROTO_LABELS, MainWindow
from abyssfs.ui.protocol_options import MAIN_PROTO_ICONS


def grab(widget: QWidget, name: str, size: QSize | None = None) -> dict:
    if size is not None:
        widget.resize(size)
    else:
        widget.adjustSize()
    widget.show()
    widget.raise_()
    QApplication.processEvents()
    pix = widget.grab()
    path = OUT / f"{name}.png"
    pix.save(str(path))
    geo = widget.geometry()
    hint = widget.sizeHint()
    return {
        "name": name,
        "file": str(path),
        "width": geo.width(),
        "height": geo.height(),
        "hint_w": hint.width(),
        "hint_h": hint.height(),
        "min_w": widget.minimumWidth(),
        "min_h": widget.minimumHeight(),
    }


def radios(dialog) -> list:
    items = []
    for rb in dialog._btn_group.buttons():
        items.append({
            "title": rb.text(),
            "proto": rb.property("proto"),
            "checked": rb.isChecked(),
            "visible": rb.isVisible(),
            "width": rb.width(),
            "height": rb.height(),
        })
    return items


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    report: dict = {"captures": [], "issues": []}

    settings = SettingsDialog(FileServerProtocol.FTP)
    settings.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    info = grab(settings, "settings_protocol_ftp", QSize(500, 640))
    report["captures"].append(info)
    settings_radios = radios(settings)
    report["settings_radios"] = settings_radios
    report["settings_hint"] = {"w": info["hint_w"], "h": info["hint_h"]}
    report["settings_ftps_status"] = settings._lbl_ftps_status.text()
    report["settings_sftp_status"] = settings._lbl_sftp_status.text()

    for idx, name in enumerate(["settings_network", "settings_protocol", "settings_plugins"]):
        settings.tabs.setCurrentIndex(idx)
        QApplication.processEvents()
        report["captures"].append(grab(settings, name, QSize(max(500, info["hint_w"]), max(640, info["hint_h"]))))

    for proto in FileServerProtocol:
        settings = SettingsDialog(proto)
        settings.tabs.setCurrentIndex(1)
        QApplication.processEvents()
        checked = [rb.property("proto") for rb in settings._btn_group.buttons() if rb.isChecked()]
        if checked != [proto.value]:
            report["issues"].append(f"SettingsDialog 当前协议 {proto.value} 未选中: {checked}")
        report["captures"].append(grab(settings, f"settings_proto_{proto.value}", QSize(560, 760)))

    proto_dlg = ProtocolDialog(FileServerProtocol.SMB)
    report["protocol_dialog_radios"] = radios(proto_dlg)
    report["captures"].append(grab(proto_dlg, "protocol_dialog_smb", QSize(480, 720)))

    user = UserDialog(default_root="C:\\data")
    report["captures"].append(grab(user, "user_dialog", QSize(UISize.USER_DIALOG_WIDTH, UISize.USER_DIALOG_HEIGHT)))
    report["user_dialog_title"] = user.windowTitle()

    from PyQt6.QtWidgets import QLabel

    vdir = VirtualDirectoryDialog()
    report["captures"].append(grab(vdir, "vdir_dialog", QSize(UISize.VDIR_DIALOG_WIDTH, UISize.VDIR_DIALOG_HEIGHT)))
    labels = [w.text() for w in vdir.findChildren(QLabel)]
    report["vdir_labels"] = labels

    labels_ok = True
    for proto in FileServerProtocol:
        if proto not in _PROTO_LABELS:
            report["issues"].append(f"主窗口缺少协议标签: {proto.value}")
            labels_ok = False
    report["main_proto_labels"] = {p.value: _PROTO_LABELS[p] for p in FileServerProtocol}

    settings_titles = [r["title"] for r in settings_radios]
    proto_titles = [r["title"] for r in report["protocol_dialog_radios"]]
    if settings_titles != proto_titles:
        report["issues"].append({
            "mismatch": "SettingsDialog vs ProtocolDialog 单选标题不一致",
            "settings": settings_titles,
            "protocol_dialog": proto_titles,
        })
    if len(settings_radios) != len(FileServerProtocol):
        report["issues"].append(
            f"设置页协议数 {len(settings_radios)} != 枚举 {len(FileServerProtocol)}"
        )

    if "FTP" not in "".join(labels):
        report["issues"].append("虚拟目录对话框文案未再出现 FTP 字样检查完成")
    ftp_legacy = [t for t in labels if "FTP" in t]
    report["vdir_ftp_legacy_copy"] = ftp_legacy

    try:
        win = MainWindow()
        win._refresh_proto_label()
        report["captures"].append(grab(win, "main_window", QSize(UISize.MAIN_WINDOW_WIDTH, UISize.MAIN_WINDOW_HEIGHT)))
        report["main_proto_text"] = win.lbl_proto.text()
        report["main_table_headers"] = [
            win.table.horizontalHeaderItem(i).text() for i in range(win.table.columnCount())
        ]
        report["main_start_text"] = win.btn_start.text()
        report["main_settings_text"] = win.btn_settings.text()
        for proto in FileServerProtocol:
            win.lbl_proto.setText(f"x {_PROTO_LABELS[proto]}")
        # cycle labels without switching service
        cycled = []
        for proto in FileServerProtocol:
            text = f"{MAIN_PROTO_ICONS[proto]} {_PROTO_LABELS[proto]}"
            win.lbl_proto.setText(text)
            QApplication.processEvents()
            cycled.append({"proto": proto.value, "text": win.lbl_proto.text(), "elided": win.lbl_proto.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, win.lbl_proto.width()) != text if win.lbl_proto.width() > 0 else False})
            report["captures"].append(grab(win, f"main_proto_{proto.value}", QSize(UISize.MAIN_WINDOW_WIDTH, UISize.MAIN_WINDOW_HEIGHT)))
        report["main_proto_cycle"] = cycled
        win.close()
    except Exception as exc:
        report["issues"].append(f"MainWindow 截取失败: {exc}")

    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "issues": report["issues"], "captures": len(report["captures"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
