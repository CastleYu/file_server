import sys
import types
import unittest

from abyssfs.protocol import ftp


class PyOpenSSLCompatTests(unittest.TestCase):
    def test_prepare_converts_attribute_error_to_import_error(self):
        broken = types.ModuleType("OpenSSL")

        def __getattr__(name: str):
            raise AttributeError("module 'lib' has no attribute 'GEN_EMAIL'")

        broken.__getattr__ = __getattr__
        saved = {
            key: sys.modules[key]
            for key in list(sys.modules)
            if key == "OpenSSL" or key.startswith("OpenSSL.")
        }
        try:
            sys.modules["OpenSSL"] = broken
            for key in list(sys.modules):
                if key.startswith("OpenSSL."):
                    del sys.modules[key]
            ftp._prepare_pyopenssl_for_pyftpdlib()
            with self.assertRaises(ImportError):
                from OpenSSL import SSL  # noqa: F401
        finally:
            for key in list(sys.modules):
                if key == "OpenSSL" or key.startswith("OpenSSL."):
                    del sys.modules[key]
            sys.modules.update(saved)

    def test_make_ftp_handler_class_imports_after_prepare(self):
        from abyssfs.fs.authorizer import _VirtualDirAuthorizer

        handler_cls = ftp._make_ftp_handler_class(
            authorizer=_VirtualDirAuthorizer(),
            banner="test",
        )
        self.assertTrue(hasattr(handler_cls, "authorizer"))
        self.assertEqual("test", handler_cls.banner)


if __name__ == "__main__":
    unittest.main()
