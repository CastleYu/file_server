import unittest
from unittest.mock import MagicMock, patch

from abyssfs.ui import constants


class LocateConsoleHwndTests(unittest.TestCase):
    def test_visible_conhost_uses_getconsolewindow(self):
        with patch.object(constants, "_get_console_window", return_value=0xABC), \
             patch.object(constants, "_window_class", return_value="ConsoleWindowClass"), \
             patch.object(constants, "_foreground_host_hwnd") as fg, \
             patch.object(constants, "_find_host_terminal_hwnd") as find_host, \
             patch("abyssfs.ui.constants.ctypes.windll", create=True) as windll:
            windll.user32.IsWindowVisible.return_value = 1
            windll.user32.IsIconic.return_value = 0
            hwnd = constants._locate_console_hwnd()
        self.assertEqual(0xABC, hwnd)
        fg.assert_not_called()
        find_host.assert_not_called()

    def test_windows_terminal_uses_foreground_host_window(self):
        with patch.object(constants, "_get_console_window", return_value=0x10), \
             patch.object(constants, "_window_class", return_value="PseudoConsoleWindow"), \
             patch.object(constants, "_foreground_host_hwnd", return_value=0x777) as fg, \
             patch.object(constants, "_find_host_terminal_hwnd") as find_host, \
             patch("abyssfs.ui.constants.ctypes.windll", create=True):
            hwnd = constants._locate_console_hwnd()
        self.assertEqual(0x777, hwnd)
        fg.assert_called_once_with(0x10)
        find_host.assert_not_called()

    def test_find_host_walks_to_windowsterminal(self):
        parents = {
            11: (22, "openconsole.exe"),
            22: (33, "windowsterminal.exe"),
            33: (1, "explorer.exe"),
        }
        with patch.object(constants, "_process_parents", return_value=parents), \
             patch.object(constants, "_window_pid", return_value=11), \
             patch.object(
                 constants,
                 "_find_titled_host_window_for_pid",
                 side_effect=lambda pid: 0x777 if pid == 22 else 0,
             ):
            self.assertEqual(0x777, constants._find_host_terminal_hwnd(0x10))


class HideShowConsoleTests(unittest.TestCase):
    def setUp(self):
        constants._console_visible = True
        constants._host_hwnd = 0
        constants._saved_exstyle = None

    def tearDown(self):
        constants._console_visible = True
        constants._host_hwnd = 0
        constants._saved_exstyle = None

    def test_hide_console_is_noop_off_windows(self):
        with patch.object(constants.os, "name", "posix"), \
             patch.object(constants, "_locate_console_hwnd") as locate:
            constants.hide_console()
        locate.assert_not_called()
        self.assertTrue(constants.is_console_visible())

    def test_hide_console_is_noop_without_hwnd(self):
        with patch.object(constants.os, "name", "nt"), \
             patch.object(constants, "_locate_console_hwnd", return_value=0), \
             patch.object(constants, "_set_console_hidden") as set_hidden:
            constants.hide_console()
        set_hidden.assert_not_called()
        self.assertTrue(constants.is_console_visible())

    def test_hide_and_show_use_located_hwnd(self):
        with patch.object(constants.os, "name", "nt"), \
             patch.object(constants, "_locate_console_hwnd", return_value=0x99) as locate, \
             patch.object(constants, "_is_host_window", return_value=True), \
             patch.object(constants, "_set_console_hidden") as set_hidden, \
             patch("abyssfs.ui.constants.ctypes.windll", create=True) as windll:
            windll.user32.IsWindow.return_value = True
            constants.hide_console()
            self.assertFalse(constants.is_console_visible())
            self.assertEqual(0x99, constants._host_hwnd)
            set_hidden.assert_called_with(0x99, True)

            constants.show_console()
            self.assertTrue(constants.is_console_visible())
            self.assertEqual(0x99, constants._host_hwnd)
            set_hidden.assert_called_with(0x99, False)
            locate.assert_called_once()

            constants.hide_console()
            self.assertFalse(constants.is_console_visible())
            self.assertEqual(3, set_hidden.call_count)
            set_hidden.assert_called_with(0x99, True)
            locate.assert_called_once()

    def test_hide_uses_sw_hide_on_host_window(self):
        user32 = MagicMock()
        user32.GetWindowLongW.return_value = constants.WS_EX_APPWINDOW
        with patch.object(constants, "_taskbar_tab") as tab, \
             patch("abyssfs.ui.constants.ctypes.windll", create=True) as windll:
            windll.user32 = user32
            constants._set_console_hidden(0x123, True)
        tab.assert_called_once_with(0x123, False)
        user32.ShowWindow.assert_called_once_with(0x123, constants.SW_HIDE)
        user32.SetWindowLongW.assert_called_once_with(
            0x123,
            constants.GWL_EXSTYLE,
            constants.WS_EX_TOOLWINDOW,
        )


if __name__ == "__main__":
    unittest.main()
