import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel

from abyssfs.protocol.base import FileServerProtocol
from abyssfs.ui.protocol_dialog import ProtocolDialog
from abyssfs.ui.protocol_options import (
    MAIN_PROTO_ICONS,
    MAIN_PROTO_LABELS,
    PROTOCOL_CHOICES,
)
from abyssfs.ui.settings_dialog import SettingsDialog
from abyssfs.ui.user_dialog import VirtualDirectoryDialog


def _app():
    return QApplication.instance() or QApplication([])


class UiProtocolSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def test_protocol_choices_cover_every_enum_member(self):
        values = [proto.value for proto, _, _ in PROTOCOL_CHOICES]
        self.assertEqual(values, [proto.value for proto in FileServerProtocol])
        self.assertEqual(set(MAIN_PROTO_LABELS), set(FileServerProtocol))
        self.assertEqual(set(MAIN_PROTO_ICONS), set(FileServerProtocol))

    def test_settings_and_protocol_dialog_radio_titles_match(self):
        settings = SettingsDialog(FileServerProtocol.FTP)
        proto_dlg = ProtocolDialog(FileServerProtocol.FTP)
        settings_titles = [rb.text() for rb in settings._btn_group.buttons()]
        dialog_titles = [rb.text() for rb in proto_dlg._btn_group.buttons()]
        expected = [title for _, title, _ in PROTOCOL_CHOICES]
        self.assertEqual(expected, settings_titles)
        self.assertEqual(expected, dialog_titles)
        self.assertTrue(settings._rb_ftp.isChecked())
        self.assertTrue(proto_dlg._rb_ftp.isChecked())
        self.assertIn("WebDAV", settings._lbl_webdav_status.text())

    def test_each_protocol_can_be_selected_in_settings(self):
        for proto in FileServerProtocol:
            dlg = SettingsDialog(proto)
            checked = [rb.property("proto") for rb in dlg._btn_group.buttons() if rb.isChecked()]
            self.assertEqual([proto.value], checked)
            self.assertGreater(dlg.minimumWidth(), 500)
            self.assertGreaterEqual(dlg.width(), 560)

    def test_original_main_window_labels_unchanged_for_legacy_protocols(self):
        self.assertEqual("FTP（明文）", MAIN_PROTO_LABELS[FileServerProtocol.FTP])
        self.assertEqual("FTPS（TLS加密）", MAIN_PROTO_LABELS[FileServerProtocol.FTPS])
        self.assertEqual("SFTP（SSH加密）", MAIN_PROTO_LABELS[FileServerProtocol.SFTP])
        self.assertEqual("SMB（文件共享）", MAIN_PROTO_LABELS[FileServerProtocol.SMB])

    def test_virtual_directory_copy_is_protocol_agnostic(self):
        dlg = VirtualDirectoryDialog()
        texts = [w.text() for w in dlg.findChildren(QLabel)]
        self.assertTrue(any("客户端中显示的名称" in text for text in texts))
        self.assertFalse(any("FTP" in text for text in texts))

    def test_network_inputs_keep_usable_width(self):
        dlg = SettingsDialog(FileServerProtocol.FTP)
        dlg.tabs.setCurrentIndex(0)
        self.assertGreaterEqual(dlg.input_max_cons_per_ip.minimumWidth(), 80)
        self.assertGreaterEqual(dlg.input_max_cons.minimumWidth(), 80)


if __name__ == "__main__":
    unittest.main()
