"""
Flask 服务器 Qt 控制示例

演示如何使用 server_base 中的基类来实现 Qt 控制的 Flask Web 应用。
"""

import sys
from typing import Dict, Any, Callable

# PyQt6 导入
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTextEdit, QGroupBox, QCheckBox, QMessageBox)
from PyQt6.QtCore import Qt

# Flask 导入
from flask import Flask
from werkzeug.serving import make_server

# 导入基类
from control.base import BaseServerContext, BaseServerThread, ServerManagerMixin


# =============================================================================
# 1. Flask 服务器上下文
# =============================================================================

class FlaskServerContext(BaseServerContext):
    """
    Flask 服务器上下文。
    
    管理 Flask 应用的配置，继承自 BaseServerContext。
    """
    
    def __init__(self, config_path: str = "flask_config.json"):
        super().__init__(config_path, default_port=5000)
        self.debug = False
        self.use_reloader = False  # 在 Qt 环境中不使用 reloader
        self.threaded = True
        self.app_name = "Flask Qt App"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "debug": self.debug,
            "threaded": self.threaded,
            "app_name": self.app_name
        }
    
    def from_dict(self, data: Dict[str, Any]) -> None:
        self.host = data.get("host", "0.0.0.0")
        self.port = data.get("port", 5000)
        self.debug = data.get("debug", False)
        self.threaded = data.get("threaded", True)
        self.app_name = data.get("app_name", "Flask Qt App")


# =============================================================================
# 2. Flask 服务器线程
# =============================================================================

class FlaskServerThread(BaseServerThread):
    """
    Flask 服务器工作线程。
    
    在后台线程运行 Flask 应用，继承自 BaseServerThread。
    使用 werkzeug 的 make_server 以支持优雅关闭。
    """
    
    def __init__(self, context: FlaskServerContext, flask_app: Flask, parent=None):
        """
        初始化 Flask 服务器线程。
        
        Args:
            context: Flask 服务器上下文
            flask_app: Flask 应用实例
            parent: 父 QObject
        """
        super().__init__(context, parent)
        self.context: FlaskServerContext = context
        self.flask_app = flask_app
        self._wsgi_server = None
    
    def setup_server(self) -> None:
        """设置 Flask/Werkzeug 服务器。"""
        host, port = self.context.get_address()
        
        # 使用 werkzeug 的 make_server 创建可控制的服务器
        self._wsgi_server = make_server(
            host=host,
            port=port,
            app=self.flask_app,
            threaded=self.context.threaded
        )
        
        self.server = self._wsgi_server
        self.status_signal.emit(f"Flask 应用 '{self.context.app_name}' 已配置")
    
    def serve(self) -> None:
        """启动 Flask 服务器主循环。"""
        self._wsgi_server.serve_forever()
    
    def stop_server(self) -> None:
        """停止 Flask 服务器。"""
        if self._wsgi_server:
            self._wsgi_server.shutdown()
            self.status_signal.emit("Flask 服务器已停止。")


# =============================================================================
# 3. 你的 Flask 应用（常规方式定义）
# =============================================================================

def create_flask_app() -> Flask:
    """
    创建 Flask 应用。
    
    这里使用常规方式定义 Flask 应用，与普通 Flask 项目完全一样。
    你可以在这里定义路由、蓝图、中间件等。
    """
    app = Flask(__name__)
    
    @app.route('/')
    def index():
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Flask Qt 控制示例</title>
            <style>
                body { font-family: 'Segoe UI', Arial, sans-serif; 
                       max-width: 800px; margin: 50px auto; padding: 20px;
                       background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                       min-height: 100vh; }
                .container { background: white; padding: 40px; border-radius: 16px;
                            box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
                h1 { color: #333; margin-bottom: 10px; }
                p { color: #666; line-height: 1.6; }
                .status { background: #e8f5e9; padding: 15px; border-radius: 8px;
                         border-left: 4px solid #4caf50; margin: 20px 0; }
                code { background: #f5f5f5; padding: 2px 6px; border-radius: 4px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎉 Flask 服务器运行中!</h1>
                <div class="status">
                    <strong>状态:</strong> 服务器正在运行，由 PyQt6 应用控制
                </div>
                <p>这是一个由 Qt 应用控制的 Flask Web 服务器示例。</p>
                <p>你可以通过 Qt 界面启动/停止这个服务器。</p>
                <h3>可用接口:</h3>
                <ul>
                    <li><code>GET /</code> - 本页面</li>
                    <li><code>GET /api/status</code> - 获取服务器状态 (JSON)</li>
                    <li><code>GET /api/hello/&lt;name&gt;</code> - 问候接口</li>
                </ul>
            </div>
        </body>
        </html>
        '''
    
    @app.route('/api/status')
    def api_status():
        return {
            "status": "running",
            "message": "Flask 服务器正常运行",
            "controlled_by": "PyQt6"
        }
    
    @app.route('/api/hello/<name>')
    def api_hello(name):
        return {
            "message": f"你好, {name}!",
            "from": "Flask Qt App"
        }
    
    return app


# =============================================================================
# 4. Qt 控制界面
# =============================================================================

class FlaskControlWindow(QMainWindow, ServerManagerMixin):
    """
    Flask 服务器控制主窗口。
    
    使用 ServerManagerMixin 获得通用的服务器管理功能。
    """
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flask 服务器控制面板 (PyQt6)")
        self.resize(600, 400)
        
        # 初始化上下文
        self.context = FlaskServerContext()
        self.context.load_from_disk()
        
        # 创建 Flask 应用（常规方式）
        self.flask_app = create_flask_app()
        
        # 服务器线程引用
        self.server_thread: FlaskServerThread | None = None
        
        self.setup_ui()
    
    def create_server_thread(self) -> FlaskServerThread:
        """创建 Flask 服务器线程实例。"""
        return FlaskServerThread(self.context, self.flask_app)
    
    def _connect_server_signals(self, thread: BaseServerThread) -> None:
        """连接服务器线程信号。"""
        thread.status_signal.connect(self.on_server_status)
        thread.error_signal.connect(self.on_server_error)
        thread.started_signal.connect(self.on_server_started)
        thread.stopped_signal.connect(self.on_server_stopped)
    
    def on_server_status(self, message: str) -> None:
        self.log_message(f"[状态] {message}")
        self.lbl_status.setText(f"状态: {message}")
    
    def on_server_error(self, message: str) -> None:
        self.log_message(f"[错误] {message}")
        QMessageBox.critical(self, "服务器错误", message)
    
    def on_server_started(self) -> None:
        self.log_message("[信息] 服务器已启动")
    
    def on_server_stopped(self) -> None:
        self.log_message("[信息] 服务器已停止")
    
    def log_message(self, message: str) -> None:
        """添加日志消息到文本框。"""
        self.txt_log.append(message)
    
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # --- 服务器控制区域 ---
        control_group = QGroupBox("服务器控制")
        control_layout = QHBoxLayout()
        
        # 端口输入
        control_layout.addWidget(QLabel("端口:"))
        self.input_port = QLineEdit(str(self.context.port))
        self.input_port.setFixedWidth(80)
        control_layout.addWidget(self.input_port)
        
        # Debug 模式
        self.chk_debug = QCheckBox("Debug 模式")
        self.chk_debug.setChecked(self.context.debug)
        control_layout.addWidget(self.chk_debug)
        
        # 启动按钮
        self.btn_start = QPushButton("启动服务器")
        self.btn_start.clicked.connect(self.on_toggle_server)
        self.btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; "
            "font-weight: bold; padding: 8px 16px;"
        )
        control_layout.addWidget(self.btn_start)
        
        # 状态标签
        self.lbl_status = QLabel("状态: 已停止")
        control_layout.addWidget(self.lbl_status)
        control_layout.addStretch()
        
        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)
        
        # --- URL 信息 ---
        url_group = QGroupBox("访问地址")
        url_layout = QHBoxLayout()
        self.lbl_url = QLabel("服务器未启动")
        self.lbl_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        url_layout.addWidget(self.lbl_url)
        url_group.setLayout(url_layout)
        main_layout.addWidget(url_group)
        
        # --- 日志区域 ---
        log_group = QGroupBox("服务器日志")
        log_layout = QVBoxLayout()
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("font-family: Consolas, monospace;")
        log_layout.addWidget(self.txt_log)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)
    
    def on_toggle_server(self):
        """切换服务器状态。"""
        if self.server_thread and self.server_thread.is_running:
            # 停止服务器
            self.stop_server()
            self.btn_start.setText("启动服务器")
            self.btn_start.setStyleSheet(
                "background-color: #4CAF50; color: white; "
                "font-weight: bold; padding: 8px 16px;"
            )
            self.lbl_status.setText("状态: 已停止")
            self.lbl_url.setText("服务器未启动")
            self.input_port.setEnabled(True)
            self.chk_debug.setEnabled(True)
        else:
            # 更新配置
            self.context.port = int(self.input_port.text())
            self.context.debug = self.chk_debug.isChecked()
            self.context.save_to_disk()
            
            # 启动服务器
            self.start_server()
            
            self.btn_start.setText("停止服务器")
            self.btn_start.setStyleSheet(
                "background-color: #F44336; color: white; "
                "font-weight: bold; padding: 8px 16px;"
            )
            self.lbl_url.setText(f"http://127.0.0.1:{self.context.port}/")
            self.input_port.setEnabled(False)
            self.chk_debug.setEnabled(False)


# =============================================================================
# 5. 程序入口
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FlaskControlWindow()
    window.show()
    sys.exit(app.exec())

