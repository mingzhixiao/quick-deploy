import sys
import os
import json
import threading
import re
import time
from pathlib import Path
import paramiko

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QProgressBar, QMessageBox, QTextEdit, QDialog,
    QTreeWidget, QTreeWidgetItem, QFormLayout, QScrollArea, QLineEdit,
    QFileDialog, QTabWidget, QGroupBox, QInputDialog, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject


# ============================================================
# 默认配置（首次运行自动生成）
# ============================================================
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "servers": {
        "local-demo": {
            "host": "127.0.0.1",
            "port": 22,
            "username": "root",
            "password": "123456"
        }
    },
    "projects": {
        "demo-project": {
            "name": "演示项目",
            "server": "local-demo",
            "pre_commands": [
                "cd D:/demo && mvn clean package"
            ],
            "files": [
                {
                    "local": "D:/demo/app.jar",
                    "remote": "/opt/demo/app.jar"
                }
            ],
            "scripts": {
                "deploy": "cd /opt/demo && ./deploy.sh",
                "restart": "cd /opt/demo && ./restart.sh",
                "status": "cd /opt/demo && ./status.sh"
            }
        }
    }
}


# ============================================================
# 全局 QSS 美化主题
# ============================================================
APP_QSS = """
QWidget {
    font-family: "Microsoft YaHei";
    font-size: 12px;
    background-color: #1e1e1e;
    color: #dddddd;
}

QComboBox, QPushButton {
    background-color: #2d2d2d;
    border: 1px solid #3c3c3c;
    padding: 5px;
    min-height: 25px;
}

QPushButton:hover {
    background-color: #3c3c3c;
}

QPushButton:pressed {
    background-color: #0e639c;
}

QTextEdit {
    background-color: #252526;
    border: 1px solid #3c3c3c;
    color: #cccccc;
}

QProgressBar {
    border: 1px solid #3c3c3c;
    background-color: #2d2d2d;
    text-align: center;
}

QProgressBar::chunk {
    background-color: #0e639c;
}

QTreeWidget {
    background-color: #252526;
    border: 1px solid #3c3c3c;
}

QLineEdit {
    background-color: #2d2d2d;
    border: 1px solid #3c3c3c;
    padding: 4px;
    color: #ffffff;
}

QGroupBox {
    border: 1px solid #3c3c3c;
    margin-top: 10px;
    padding-top: 10px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}

QTabWidget::pane {
    border: 1px solid #3c3c3c;
}

QTabBar::tab {
    background-color: #2d2d2d;
    border: 1px solid #3c3c3c;
    padding: 5px 10px;
}

QTabBar::tab:selected {
    background-color: #0e639c;
}
"""


# ============================================================
# 配置文件工具
# ============================================================
def ensure_config_exists():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)


def load_full_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_full_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ============================================================
# SSH 操作信号
# ============================================================
class SSHSignals(QObject):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)


# ============================================================
# SSH 操作工具函数
# ============================================================
def execute_local_commands(commands, signals, stop_flag=None):
    """执行本地前置命令"""
    import subprocess
    import sys
    import locale
    
    # 获取系统默认编码
    if os.name == 'nt':
        # Windows 下使用 GBK 或系统默认编码
        default_encoding = locale.getpreferredencoding() or 'gbk'
    else:
        default_encoding = 'utf-8'
    
    for i, cmd in enumerate(commands, 1):
        if stop_flag and stop_flag.get('stop'):
            signals.log.emit("🛑 操作已停止")
            return False

        signals.log.emit(f"执行前置命令 [{i}/{len(commands)}]: {cmd}")
        
        try:
            # 在 Windows 上使用 cmd，在 Linux/Mac 上使用 bash
            if os.name == 'nt':
                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    # 移除文本模式参数，使用二进制读取
                )
            else:
                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    executable='/bin/bash',
                    bufsize=1,
                    # 移除文本模式参数，使用二进制读取
                )
            
            # 实时读取输出（二进制模式）
            for line_bytes in iter(process.stdout.readline, b''):
                if stop_flag and stop_flag.get('stop'):
                    process.terminate()
                    signals.log.emit("🛑 操作已停止，正在终止进程...")
                    return False
                
                if line_bytes:
                    # 尝试解码
                    try:
                        # 优先尝试系统默认编码（如GBK）
                        line = line_bytes.decode(default_encoding).rstrip()
                    except UnicodeDecodeError:
                        try:
                            # 失败则尝试 UTF-8
                            line = line_bytes.decode('utf-8').rstrip()
                        except UnicodeDecodeError:
                            # 最后使用 replace 策略
                            line = line_bytes.decode(default_encoding, errors='replace').rstrip()
                            
                    signals.log.emit(f"  {line}")
            
            # 等待命令完成
            process.wait()
            return_code = process.returncode
            
            if return_code != 0:
                # 如果是手动停止导致的非0退出，不报错
                if stop_flag and stop_flag.get('stop'):
                    return False
                signals.log.emit(f"✗ 命令执行失败，退出码: {return_code}")
                return False
            else:
                signals.log.emit(f"✓ 命令执行成功")
        
        except Exception as e:
            signals.log.emit(f"✗ 命令执行异常: {str(e)}")
            return False
    
    return True


def mkdir_recursive(sftp, remote_path):
    """递归创建远程目录"""
    parts = remote_path.split("/")
    path = ""
    for part in parts:
        if not part:
            continue
        path += "/" + part
        try:
            sftp.stat(path)
        except IOError:
            sftp.mkdir(path)


def upload_file_to_server(sftp, local_path, remote_path, signals):
    """上传单个文件，带进度显示"""
    mkdir_recursive(sftp, os.path.dirname(remote_path))
    
    # 获取文件大小
    file_size = os.path.getsize(local_path)
    last_percent = [0]  # 记录上次显示的百分比
    
    def progress_callback(transferred, total):
        """上传进度回调"""
        if total == 0:
            return
        
        percent = int(transferred / total * 100)
        
        # 每增加 10% 或完成时输出一次日志
        if percent >= last_percent[0] + 10 or transferred == total:
            last_percent[0] = percent
            mb_transferred = transferred / 1024 / 1024
            mb_total = total / 1024 / 1024
            signals.log.emit(f"  上传进度: {percent}% ({mb_transferred:.2f}MB / {mb_total:.2f}MB)")
    
    file_name = os.path.basename(local_path)
    file_size_mb = file_size / 1024 / 1024
    signals.log.emit(f"开始上传: {file_name} ({file_size_mb:.2f}MB)")
    
    sftp.put(local_path, remote_path, callback=progress_callback)
    signals.progress.emit(1)
    signals.log.emit(f"✓ 上传完成: {file_name} -> {remote_path}")




def upload_project_files_worker(server_cfg, project_cfg, signals, stop_flag=None):
    """上传项目配置的所有文件"""
    try:
        # 执行前置命令（如果有）
        pre_commands = project_cfg.get("pre_commands", [])
        if pre_commands:
            signals.log.emit("=" * 60)
            signals.log.emit("执行前置命令...")
            signals.log.emit("=" * 60)
            if not execute_local_commands(pre_commands, signals, stop_flag):
                signals.finished.emit(False, "前置命令执行失败或被停止")
                return
            
            if stop_flag and stop_flag.get('stop'):
                return

            signals.log.emit("=" * 60)
            signals.log.emit("前置命令执行完成")
            signals.log.emit("=" * 60)
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            server_cfg["host"],
            int(server_cfg["port"]),
            server_cfg["username"],
            server_cfg["password"]
        )
        signals.log.emit(f"✓ SSH 连接成功: {server_cfg['host']}")

        sftp = ssh.open_sftp()

        files = project_cfg.get("files", [])
        if not files:
            signals.finished.emit(False, "项目未配置任何文件")
            return

        total_files = len(files)
        signals.progress.emit(0)
        signals.log.emit(f"开始上传项目文件，共 {total_files} 个文件...")

        for file_info in files:
            if stop_flag and stop_flag.get('stop'):
                signals.log.emit("🛑 上传已停止")
                sftp.close()
                ssh.close()
                signals.finished.emit(False, "操作已停止")
                return

            local_path = file_info.get("local", "")
            remote_path = file_info.get("remote", "")
            
            if not local_path or not remote_path:
                signals.log.emit(f"⚠ 跳过无效配置: {file_info}")
                continue
            
            if not os.path.exists(local_path):
                signals.log.emit(f"✗ 本地文件不存在: {local_path}")
                continue
            
            # 如果远程路径以 / 结尾，说明是目录，需要添加文件名
            if remote_path.endswith("/"):
                remote_path = remote_path + os.path.basename(local_path)
            
            upload_file_to_server(sftp, local_path, remote_path, signals)


        sftp.close()
        ssh.close()

        signals.finished.emit(True, f"文件上传完成，共 {total_files} 个文件")
    except Exception as e:
        signals.finished.emit(False, f"上传失败: {str(e)}")


def full_deploy_worker(server_cfg, project_cfg, signals, stop_flag=None):
    """完整部署流程：上传文件 + 执行部署脚本"""
    try:
        # 执行前置命令（如果有）
        pre_commands = project_cfg.get("pre_commands", [])
        if pre_commands:
            signals.log.emit("=" * 60)
            signals.log.emit("执行前置命令...")
            signals.log.emit("=" * 60)
            if not execute_local_commands(pre_commands, signals, stop_flag):
                signals.finished.emit(False, "前置命令执行失败或被停止")
                return
            
            if stop_flag and stop_flag.get('stop'):
                return

            signals.log.emit("=" * 60)
            signals.log.emit("前置命令执行完成")
            signals.log.emit("=" * 60)
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            server_cfg["host"],
            int(server_cfg["port"]),
            server_cfg["username"],
            server_cfg["password"]
        )
        signals.log.emit(f"✓ SSH 连接成功: {server_cfg['host']}")

        sftp = ssh.open_sftp()

        files = project_cfg.get("files", [])
        if not files:
            signals.finished.emit(False, "项目未配置任何文件")
            return

        total_files = len(files)
        signals.progress.emit(0)
        signals.log.emit(f"开始上传项目文件，共 {total_files} 个文件...")

        for file_info in files:
            if stop_flag and stop_flag.get('stop'):
                signals.log.emit("🛑 部署已停止")
                sftp.close()
                ssh.close()
                signals.finished.emit(False, "操作已停止")
                return

            local_path = file_info.get("local", "")
            remote_path = file_info.get("remote", "")
            
            if not local_path or not remote_path:
                signals.log.emit(f"⚠ 跳过无效配置: {file_info}")
                continue
            
            if not os.path.exists(local_path):
                signals.log.emit(f"✗ 本地文件不存在: {local_path}")
                continue
            
            # 如果远程路径以 / 结尾，说明是目录，需要添加文件名
            if remote_path.endswith("/"):
                remote_path = remote_path + os.path.basename(local_path)
            
            upload_file_to_server(sftp, local_path, remote_path, signals)


        sftp.close()
        
        if stop_flag and stop_flag.get('stop'):
            signals.log.emit("🛑 部署已停止")
            ssh.close()
            signals.finished.emit(False, "操作已停止")
            return

        signals.log.emit("上传完成，开始执行部署脚本...")

        deploy_script = project_cfg.get("scripts", {}).get("deploy", "")
        if deploy_script:
            signals.log.emit(f"执行命令: {deploy_script}")
            # 使用 get_pty=True 获取实时输出
            stdin, stdout, stderr = ssh.exec_command(deploy_script, get_pty=True)
            
            # 实时读取输出
            while True:
                if stop_flag and stop_flag.get('stop'):
                    signals.log.emit("🛑 操作已停止")
                    # 尝试发送 Ctrl+C
                    stdin.write('\x03')
                    stdin.channel.close()
                    ssh.close()
                    signals.finished.emit(False, "操作已停止")
                    return

                if stdout.channel.recv_ready():
                    line = stdout.readline()
                    if line:
                        signals.log.emit(line.rstrip())
                    else:
                        break
                elif stdout.channel.exit_status_ready():
                    break
                else:
                    time.sleep(0.1)

        ssh.close()

        signals.finished.emit(True, "部署完成")
    except Exception as e:
        signals.finished.emit(False, f"部署失败: {str(e)}")


def upload_single_file_worker(server_cfg, local_file, remote_file, signals):
    """上传单个文件"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            server_cfg["host"],
            int(server_cfg["port"]),
            server_cfg["username"],
            server_cfg["password"]
        )
        signals.log.emit(f"✓ SSH 连接成功: {server_cfg['host']}")

        sftp = ssh.open_sftp()
        upload_file_to_server(sftp, local_file, remote_file, signals)

        sftp.close()
        ssh.close()

        signals.finished.emit(True, "文件上传完成")
    except Exception as e:
        signals.finished.emit(False, f"上传失败: {str(e)}")


def execute_script_worker(server_cfg, script_cmd, signals, stop_flag=None):
    """执行远程脚本，实时输出日志"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            server_cfg["host"],
            int(server_cfg["port"]),
            server_cfg["username"],
            server_cfg["password"]
        )
        signals.log.emit(f"✓ SSH 连接成功: {server_cfg['host']}")
        
        # 使用临时文件避免引号问题
        # 1. 创建临时脚本文件
        timestamp = int(time.time())
        script_file = f"/tmp/deploy_script_{timestamp}.sh"
        log_file = f"/tmp/deploy_log_{timestamp}.log"
        done_file = f"/tmp/deploy_done_{timestamp}.flag"
        
        signals.log.emit(f"执行命令: {script_cmd}")
        signals.log.emit("=" * 60)

        # 写入脚本内容
        script_content = f"""#!/bin/bash
source /etc/profile 2>/dev/null || true
source ~/.bashrc 2>/dev/null || true
source ~/.bash_profile 2>/dev/null || true
{script_cmd}
exit_code=$?
echo "=== 脚本执行完成，退出码: $exit_code ==="
echo $exit_code > {done_file}
exit $exit_code
"""
        create_cmd = f"cat > {script_file} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT\nchmod +x {script_file}"
        ssh.exec_command(create_cmd)
        
        # 将脚本放到后台执行，输出到日志文件
        bg_cmd = f"nohup {script_file} > {log_file} 2>&1 &"
        signals.log.emit("脚本已在后台启动，正在读取日志...")
        
        # 执行后台命令
        stdin, stdout, stderr = ssh.exec_command(bg_cmd)
        stdout.channel.recv_exit_status()
        
        # 等待日志文件创建
        time.sleep(1)
        
        # 使用 tail -f 实时读取日志文件
        tail_cmd = f"tail -f {log_file}"
        stdin, stdout, stderr = ssh.exec_command(tail_cmd, get_pty=True)
        
        # 设置超时时间（10分钟）
        start_time = time.time()
        timeout = 600
        last_line_time = start_time
        check_interval = 2  # 每2秒检查一次完成标记
        last_check_time = start_time
        
        while True:
            # 停止检查
            if stop_flag and stop_flag.get('stop'):
                signals.log.emit("🛑 操作已停止，正在清理...")
                break

            current_time = time.time()
            
            # 每2秒检查一次完成标记文件
            if current_time - last_check_time >= check_interval:
                # 检查完成标记文件是否存在
                stdin_check, stdout_check, stderr_check = ssh.exec_command(f"test -f {done_file} && echo 'DONE'")
                check_result = stdout_check.read().decode('utf-8', errors='ignore').strip()
                if check_result == 'DONE':
                    signals.log.emit("检测到脚本执行完成标记")
                    time.sleep(1)  # 等待1秒确保所有日志都输出
                    break
                last_check_time = current_time
            
            # 检查是否超时（超过60秒没有新输出）
            if current_time - last_line_time > 60:
                signals.log.emit("日志输出超时（60秒无新输出），脚本可能已执行完成")
                break
            
            # 检查总超时
            if current_time - start_time > timeout:
                signals.log.emit("执行超时（10分钟）")
                break
            
            # 非阻塞读取
            if stdout.channel.recv_ready():
                line = stdout.readline()
                if line:
                    last_line_time = current_time
                    # 去除 ANSI 颜色代码
                    clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line.rstrip())
                    if clean_line:
                        signals.log.emit(clean_line)
            else:
                time.sleep(0.1)
        
        # 停止 tail 命令
        try:
            stdout.channel.close()
        except:
            pass
        
        # 读取退出码
        stdin, stdout, stderr = ssh.exec_command(f"cat {done_file} 2>/dev/null || echo '0'")
        script_exit_code = stdout.read().decode('utf-8', errors='ignore').strip()
        
        # 清理临时文件
        ssh.exec_command(f"rm -f {script_file} {log_file} {done_file}")
        
        signals.log.emit("=" * 60)
        
        ssh.close()

        if stop_flag and stop_flag.get('stop'):
            signals.finished.emit(False, "操作已停止")
            return

        if script_exit_code == '0':
            signals.finished.emit(True, "脚本执行完成")
        else:
            signals.finished.emit(False, f"脚本执行失败，退出码: {script_exit_code}")
    except Exception as e:
        signals.finished.emit(False, f"执行失败: {str(e)}")









# ============================================================
# 配置编辑器
# ============================================================
class ConfigEditor(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置管理器")
        self.resize(1000, 700)

        ensure_config_exists()
        self.config = load_full_config()

        layout = QVBoxLayout(self)

        # 标签页
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # 服务器配置页
        self.server_tab = QWidget()
        self.init_server_tab()
        self.tabs.addTab(self.server_tab, "服务器配置")

        # 项目配置页
        self.project_tab = QWidget()
        self.init_project_tab()
        self.tabs.addTab(self.project_tab, "项目配置")

        # 底部按钮
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("保存所有配置")
        self.btn_save.clicked.connect(self.save_all)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

    def init_server_tab(self):
        layout = QHBoxLayout(self.server_tab)

        # 左侧服务器列表
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("服务器列表"))
        self.server_list = QTreeWidget()
        self.server_list.setHeaderLabels(["服务器名称"])
        self.server_list.itemClicked.connect(self.on_server_selected)
        # 启用右键菜单
        self.server_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.server_list.customContextMenuRequested.connect(self.show_server_context_menu)
        left_layout.addWidget(self.server_list)

        btn_layout = QHBoxLayout()
        btn_add_server = QPushButton("新增服务器")
        btn_add_server.clicked.connect(self.add_server)
        btn_del_server = QPushButton("删除服务器")
        btn_del_server.clicked.connect(self.delete_server)
        btn_layout.addWidget(btn_add_server)
        btn_layout.addWidget(btn_del_server)
        left_layout.addLayout(btn_layout)

        layout.addLayout(left_layout, 3)

        # 右侧服务器详情
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("服务器详情"))

        self.server_form_area = QScrollArea()
        self.server_form_area.setWidgetResizable(True)
        self.server_form_widget = QWidget()
        self.server_form_layout = QFormLayout(self.server_form_widget)
        self.server_form_area.setWidget(self.server_form_widget)
        right_layout.addWidget(self.server_form_area)

        layout.addLayout(right_layout, 7)

        self.server_fields = {}
        self.current_server = None
        self.load_server_list()

    def init_project_tab(self):
        layout = QHBoxLayout(self.project_tab)

        # 左侧项目列表
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("项目列表"))
        self.project_list = QTreeWidget()
        self.project_list.setHeaderLabels(["项目名称"])
        self.project_list.itemClicked.connect(self.on_project_selected)
        # 启用右键菜单
        self.project_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self.show_project_context_menu)
        left_layout.addWidget(self.project_list)

        btn_layout = QHBoxLayout()
        btn_add_project = QPushButton("新增项目")
        btn_add_project.clicked.connect(self.add_project)
        btn_del_project = QPushButton("删除项目")
        btn_del_project.clicked.connect(self.delete_project)
        btn_layout.addWidget(btn_add_project)
        btn_layout.addWidget(btn_del_project)
        left_layout.addLayout(btn_layout)

        layout.addLayout(left_layout, 3)

        # 右侧项目详情
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("项目详情"))

        self.project_form_area = QScrollArea()
        self.project_form_area.setWidgetResizable(True)
        self.project_form_widget = QWidget()
        self.project_form_layout = QFormLayout(self.project_form_widget)
        self.project_form_area.setWidget(self.project_form_widget)
        right_layout.addWidget(self.project_form_area)

        layout.addLayout(right_layout, 7)

        self.project_fields = {}
        self.current_project = None
        self.load_project_list()

    def load_server_list(self):
        self.server_list.clear()
        for server_name in self.config.get("servers", {}).keys():
            QTreeWidgetItem(self.server_list, [server_name])

    def load_project_list(self):
        self.project_list.clear()
        for project_name in self.config.get("projects", {}).keys():
            QTreeWidgetItem(self.project_list, [project_name])

    def clear_form(self, form_layout, fields_dict):
        while form_layout.count():
            item = form_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        fields_dict.clear()

    def on_server_selected(self, item):
        server_name = item.text(0)
        self.current_server = server_name
        self.render_server_form(server_name)

    def render_server_form(self, server_name):
        self.clear_form(self.server_form_layout, self.server_fields)

        server_data = self.config["servers"][server_name]

        # 服务器名称
        name_edit = QLineEdit(server_name)
        self.server_form_layout.addRow(QLabel("服务器名称"), name_edit)
        self.server_fields["_name"] = name_edit

        # 其他字段
        for key, value in server_data.items():
            edit = QLineEdit(str(value))
            if key == "password":
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.server_form_layout.addRow(QLabel(key), edit)
            self.server_fields[key] = edit

        # 测试连接按钮
        btn_test = QPushButton("测试 SSH 连接")
        btn_test.clicked.connect(lambda: self.test_ssh_connection(server_name))
        self.server_form_layout.addRow(btn_test)

    def on_project_selected(self, item):
        project_name = item.text(0)
        self.current_project = project_name
        self.render_project_form(project_name)

    def render_project_form(self, project_name):
        self.clear_form(self.project_form_layout, self.project_fields)

        project_data = self.config["projects"][project_name]

        # 项目 ID
        id_edit = QLineEdit(project_name)
        self.project_form_layout.addRow(QLabel("项目 ID"), id_edit)
        self.project_fields["_id"] = id_edit

        # 项目名称
        name_edit = QLineEdit(project_data.get("name", ""))
        self.project_form_layout.addRow(QLabel("项目名称"), name_edit)
        self.project_fields["name"] = name_edit

        # 关联服务器
        server_combo = QComboBox()
        server_combo.addItems(self.config.get("servers", {}).keys())
        current_server = project_data.get("server", "")
        if current_server in self.config.get("servers", {}):
            server_combo.setCurrentText(current_server)
        self.project_form_layout.addRow(QLabel("关联服务器"), server_combo)
        self.project_fields["server"] = server_combo

        # 文件配置区域
        self.project_form_layout.addRow(QLabel(""), QLabel(""))  # 空行
        files_label = QLabel("文件配置（本地路径 -> 远程路径）")
        files_label.setStyleSheet("font-weight: bold;")
        self.project_form_layout.addRow(files_label)

        # 文件列表
        files = project_data.get("files", [])
        self.project_fields["files"] = []
        
        for idx, file_info in enumerate(files):
            self.add_file_row(idx, file_info.get("local", ""), file_info.get("remote", ""), init=True)

        # 记录"+ 添加文件"按钮的位置
        self.file_add_button_row = self.project_form_layout.rowCount()
        btn_add_file = QPushButton("+ 添加文件")
        btn_add_file.clicked.connect(self.add_file_row_empty)
        self.project_form_layout.addRow(btn_add_file)

        # 前置命令配置
        self.pre_cmd_section_start = self.project_form_layout.rowCount()
        self.project_form_layout.addRow(QLabel(""), QLabel(""))  # 空行
        pre_cmd_label = QLabel("前置命令（上传前执行的本地命令）")
        pre_cmd_label.setStyleSheet("font-weight: bold;")
        self.project_form_layout.addRow(pre_cmd_label)
        
        pre_commands = project_data.get("pre_commands", [])
        self.project_fields["pre_commands"] = []
        
        for idx, cmd in enumerate(pre_commands):
            self.add_pre_command_row(idx, cmd, init=True)
        
        # 记录"+ 添加命令"按钮的位置
        self.pre_cmd_add_button_row = self.project_form_layout.rowCount()
        btn_add_cmd = QPushButton("+ 添加命令")
        btn_add_cmd.clicked.connect(self.add_pre_command_row_empty)
        self.project_form_layout.addRow(btn_add_cmd)

        # 脚本配置
        self.script_section_start = self.project_form_layout.rowCount()
        self.project_form_layout.addRow(QLabel(""), QLabel(""))  # 空行
        scripts_label = QLabel("脚本配置")
        scripts_label.setStyleSheet("font-weight: bold;")
        self.project_form_layout.addRow(scripts_label)
        
        scripts = project_data.get("scripts", {})
        
        deploy_edit = QLineEdit(scripts.get("deploy", ""))
        self.project_form_layout.addRow(QLabel("部署脚本"), deploy_edit)
        self.project_fields["script_deploy"] = deploy_edit

        restart_edit = QLineEdit(scripts.get("restart", ""))
        self.project_form_layout.addRow(QLabel("重启脚本"), restart_edit)
        self.project_fields["script_restart"] = restart_edit

        status_edit = QLineEdit(scripts.get("status", ""))
        self.project_form_layout.addRow(QLabel("状态脚本"), status_edit)
        self.project_fields["script_status"] = status_edit

    def add_file_row(self, idx=None, local_path="", remote_path="", init=False):
        """添加文件配置行"""
        if idx is None:
            idx = len(self.project_fields.get("files", []))
        
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        # 本地文件
        local_edit = QLineEdit(local_path)
        local_edit.setPlaceholderText("本地文件路径")
        row_layout.addWidget(local_edit, 3)

        # 浏览按钮
        btn_browse = QPushButton("浏览")
        btn_browse.clicked.connect(lambda: self.browse_file(local_edit))
        row_layout.addWidget(btn_browse)

        # 箭头标签
        arrow_label = QLabel("→")
        row_layout.addWidget(arrow_label)

        # 远程文件
        remote_edit = QLineEdit(remote_path)
        remote_edit.setPlaceholderText("远程文件路径")
        row_layout.addWidget(remote_edit, 3)

        # 删除按钮
        btn_delete = QPushButton("删除")
        btn_delete.clicked.connect(lambda: self.remove_file_row(row_widget))
        row_layout.addWidget(btn_delete)

        if init:
            # 初始化时直接添加到末尾
            self.project_form_layout.addRow(row_widget)
        else:
            # 动态添加时插入到"+ 添加文件"按钮之前
            insert_pos = self.file_add_button_row
            self.project_form_layout.insertRow(insert_pos, row_widget)
            self.file_add_button_row += 1  # 更新按钮位置
            self.pre_cmd_section_start += 1  # 更新后续区域位置
            self.pre_cmd_add_button_row += 1
            self.script_section_start += 1
        
        self.project_fields["files"].append({
            "widget": row_widget,
            "local": local_edit,
            "remote": remote_edit
        })

    def add_file_row_empty(self):
        """添加空的文件配置行"""
        self.add_file_row()

    def remove_file_row(self, row_widget):
        """删除文件配置行"""
        # 从布局中移除
        for i in range(self.project_form_layout.count()):
            item = self.project_form_layout.itemAt(i)
            if item and item.widget() == row_widget:
                self.project_form_layout.removeRow(i)
                break
        
        # 从字段列表中移除
        self.project_fields["files"] = [
            f for f in self.project_fields.get("files", [])
            if f["widget"] != row_widget
        ]
        
        # 删除widget
        row_widget.deleteLater()

    def add_pre_command_row(self, idx=None, command="", init=False):
        """添加前置命令行"""
        if idx is None:
            idx = len(self.project_fields.get("pre_commands", []))
        
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        # 命令输入框
        cmd_edit = QLineEdit(command)
        cmd_edit.setPlaceholderText("例如: cd D:/project && mvn clean package")
        row_layout.addWidget(cmd_edit, 1)

        # 删除按钮
        btn_delete = QPushButton("删除")
        btn_delete.clicked.connect(lambda: self.remove_pre_command_row(row_widget))
        row_layout.addWidget(btn_delete)

        if init:
            # 初始化时直接添加到末尾
            self.project_form_layout.addRow(row_widget)
        else:
            # 动态添加时插入到"+ 添加命令"按钮之前
            insert_pos = self.pre_cmd_add_button_row
            self.project_form_layout.insertRow(insert_pos, row_widget)
            self.pre_cmd_add_button_row += 1  # 更新按钮位置
            self.script_section_start += 1  # 更新后续区域位置
        
        self.project_fields["pre_commands"].append({
            "widget": row_widget,
            "command": cmd_edit
        })

    def add_pre_command_row_empty(self):
        """添加空的前置命令行"""
        self.add_pre_command_row()

    def remove_pre_command_row(self, row_widget):
        """删除前置命令行"""
        # 从布局中移除
        for i in range(self.project_form_layout.count()):
            item = self.project_form_layout.itemAt(i)
            if item and item.widget() == row_widget:
                self.project_form_layout.removeRow(i)
                break
        
        # 从字段列表中移除
        self.project_fields["pre_commands"] = [
            c for c in self.project_fields.get("pre_commands", [])
            if c["widget"] != row_widget
        ]
        
        # 删除widget
        row_widget.deleteLater()

    def browse_file(self, line_edit):
        """浏览选择文件"""
        file_path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if file_path:
            line_edit.setText(file_path)


    def browse_directory(self, line_edit):
        directory = QFileDialog.getExistingDirectory(self, "选择目录")
        if directory:
            line_edit.setText(directory)

    def add_server(self):
        name = "new-server"
        i = 1
        while name in self.config.get("servers", {}):
            name = f"new-server-{i}"
            i += 1

        self.config.setdefault("servers", {})[name] = {
            "host": "",
            "port": 22,
        }
        save_full_config(self.config)
        self.load_server_list()

    def delete_server(self):
        if not self.current_server:
            QMessageBox.warning(self, "提示", "请先选择要删除的服务器")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("确认")
        msg.setText(f"确定删除服务器 '{self.current_server}' 吗？")
        msg.setIcon(QMessageBox.Icon.Question)
        
        yes_btn = msg.addButton("是", QMessageBox.ButtonRole.YesRole)
        no_btn = msg.addButton("否", QMessageBox.ButtonRole.NoRole)
        
        # 增加按钮宽度和间距
        msg.setStyleSheet("QPushButton { min-width: 60px; padding: 5px 15px; margin-left: 10px; font-family: 'Microsoft YaHei'; }")
        
        msg.exec()
        
        if msg.clickedButton() == yes_btn:
            del self.config["servers"][self.current_server]
            save_full_config(self.config)
            self.current_server = None
            self.clear_form(self.server_form_layout, self.server_fields)
            self.load_server_list()

    def add_project(self):
        name = "new-project"
        i = 1
        while name in self.config.get("projects", {}):
            name = f"new-project-{i}"
            i += 1

        first_server = list(self.config.get("servers", {}).keys())[0] if self.config.get("servers") else ""

        self.config.setdefault("projects", {})[name] = {
            "name": "新项目",
            "server": first_server,
            "pre_commands": [],
            "files": [],
            "scripts": {
                "deploy": "",
                "restart": "",
                "status": ""
            }
        }
        save_full_config(self.config)
        self.load_project_list()

    def delete_project(self):
        if not self.current_project:
            QMessageBox.warning(self, "提示", "请先选择要删除的项目")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("确认")
        msg.setText(f"确定删除项目 '{self.current_project}' 吗？")
        msg.setIcon(QMessageBox.Icon.Question)
        
        yes_btn = msg.addButton("是", QMessageBox.ButtonRole.YesRole)
        no_btn = msg.addButton("否", QMessageBox.ButtonRole.NoRole)
        
        # 增加按钮宽度和间距
        msg.setStyleSheet("QPushButton { min-width: 60px; padding: 5px 15px; margin-left: 10px; font-family: 'Microsoft YaHei'; }")
        
        msg.exec()
        
        if msg.clickedButton() == yes_btn:
            del self.config["projects"][self.current_project]
            save_full_config(self.config)
            self.current_project = None
            self.clear_form(self.project_form_layout, self.project_fields)
            self.load_project_list()

    def test_ssh_connection(self, server_name):
        # 从表单字段读取当前填写的值，而不是从配置文件读取
        if not self.server_fields:
            QMessageBox.warning(self, "提示", "请先选择或编辑服务器配置")
            return
        
        try:
            host = self.server_fields.get("host").text().strip()
            port = int(self.server_fields.get("port").text().strip())
            username = self.server_fields.get("username").text().strip()
            password = self.server_fields.get("password").text().strip()
            
            if not host or not username:
                QMessageBox.warning(self, "提示", "主机地址和用户名不能为空")
                return
            
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, port, username, password, timeout=5)
            ssh.close()
            QMessageBox.information(self, "成功", f"成功连接到服务器: {host}")
        except ValueError as e:
            QMessageBox.critical(self, "失败", f"端口号格式错误: {str(e)}")
        except Exception as e:
            QMessageBox.critical(self, "失败", f"连接失败:\n{str(e)}")


    def show_server_context_menu(self, pos):
        item = self.server_list.itemAt(pos)
        if not item:
            return
            
        menu = QMenu()
        dup_action = menu.addAction("复制服务器")
        action = menu.exec(self.server_list.mapToGlobal(pos))
        
        if action == dup_action:
            self.duplicate_server(item.text(0))
            
    def duplicate_server(self, server_name):
        import copy
        
        new_name = f"{server_name}-复制"
        # 避免重名
        idx = 1
        while new_name in self.config.get("servers", {}):
            new_name = f"{server_name}-复制{idx}"
            idx += 1
            
        if server_name in self.config.get("servers", {}):
            new_config = copy.deepcopy(self.config["servers"][server_name])
            self.config.setdefault("servers", {})[new_name] = new_config
            save_full_config(self.config)
            
            # 刷新列表并选中
            self.load_server_list()
            items = self.server_list.findItems(new_name, Qt.MatchFlag.MatchExactly)
            if items:
                self.server_list.setCurrentItem(items[0])
                self.on_server_selected(items[0])
                
    def show_project_context_menu(self, pos):
        item = self.project_list.itemAt(pos)
        if not item:
            return
            
        menu = QMenu()
        dup_action = menu.addAction("复制项目")
        action = menu.exec(self.project_list.mapToGlobal(pos))
        
        if action == dup_action:
            self.duplicate_project(item.text(0))
            
    def duplicate_project(self, project_name):
        import copy
        
        new_name = f"{project_name}-复制"
        # 避免重名
        idx = 1
        while new_name in self.config.get("projects", {}):
            new_name = f"{project_name}-复制{idx}"
            idx += 1
            
        if project_name in self.config.get("projects", {}):
            new_config = copy.deepcopy(self.config["projects"][project_name])
            new_config["name"] = new_name # 更新内部名称
            self.config.setdefault("projects", {})[new_name] = new_config
            save_full_config(self.config)
            
            # 刷新列表并选中
            self.load_project_list()
            items = self.project_list.findItems(new_name, Qt.MatchFlag.MatchExactly)
            if items:
                self.project_list.setCurrentItem(items[0])
                self.on_project_selected(items[0])

    def save_all(self):
        # 保存当前编辑的服务器
        if self.current_server and self.server_fields:
            new_name = self.server_fields["_name"].text().strip()
            if not new_name:
                QMessageBox.warning(self, "提示", "服务器名称不能为空")
                return

            # 检查新名称是否与其他服务器冲突
            if new_name != self.current_server and new_name in self.config.get("servers", {}):
                QMessageBox.warning(self, "提示", f"服务器名称 '{new_name}' 已存在")
                return

            server_data = {}
            for key, edit in self.server_fields.items():
                if key == "_name":
                    continue
                server_data[key] = edit.text().strip()

            # 先添加新配置，再删除旧配置（避免 KeyError）
            self.config.setdefault("servers", {})[new_name] = server_data
            if new_name != self.current_server:
                del self.config["servers"][self.current_server]
                # 更新所有引用此服务器的项目
                for project_data in self.config.get("projects", {}).values():
                    if project_data.get("server") == self.current_server:
                        project_data["server"] = new_name
                self.current_server = new_name

        # 保存当前编辑的项目
        if self.current_project and self.project_fields:
            new_id = self.project_fields["_id"].text().strip()
            if not new_id:
                QMessageBox.warning(self, "提示", "项目 ID 不能为空")
                return

            # 检查新 ID 是否与其他项目冲突
            if new_id != self.current_project and new_id in self.config.get("projects", {}):
                QMessageBox.warning(self, "提示", f"项目 ID '{new_id}' 已存在")
                return

            # 收集文件配置
            files = []
            for file_row in self.project_fields.get("files", []):
                local = file_row["local"].text().strip()
                remote = file_row["remote"].text().strip()
                if local and remote:  # 只保存非空的配置
                    files.append({
                        "local": local,
                        "remote": remote
                    })

            # 收集前置命令
            pre_commands = []
            for cmd_row in self.project_fields.get("pre_commands", []):
                cmd = cmd_row["command"].text().strip()
                if cmd:  # 只保存非空的命令
                    pre_commands.append(cmd)

            project_data = {
                "name": self.project_fields["name"].text().strip(),
                "server": self.project_fields["server"].currentText(),
                "pre_commands": pre_commands,
                "files": files,
                "scripts": {
                    "deploy": self.project_fields["script_deploy"].text().strip(),
                    "restart": self.project_fields["script_restart"].text().strip(),
                    "status": self.project_fields["script_status"].text().strip()
                }
            }

            # 先添加新配置，再删除旧配置（避免 KeyError）
            self.config.setdefault("projects", {})[new_id] = project_data
            if new_id != self.current_project:
                del self.config["projects"][self.current_project]
                self.current_project = new_id

        save_full_config(self.config)
        QMessageBox.information(self, "成功", "配置已保存")
        self.load_server_list()
        self.load_project_list()


# ============================================================
# 主界面
# ============================================================
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("项目部署工具")
        self.resize(900, 600)

        ensure_config_exists()
        self.config = load_full_config()

        self.signals = SSHSignals()
        self.signals.progress.connect(self.on_progress)
        self.signals.log.connect(self.on_log)
        self.signals.finished.connect(self.on_finished)
        
        # 停止标志（使用字典以便在线程间共享）
        self.stop_flag = {'stop': False}
        self.current_thread = None

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 项目选择
        project_group = QGroupBox("项目选择")
        project_layout = QHBoxLayout()
        project_layout.addWidget(QLabel("项目："))
        self.combo_project = QComboBox()
        self.combo_project.currentTextChanged.connect(self.on_project_changed)
        project_layout.addWidget(self.combo_project, 1)
        project_group.setLayout(project_layout)
        layout.addWidget(project_group)

        # 项目信息显示
        info_group = QGroupBox("项目信息")
        info_layout = QFormLayout()
        self.lbl_project_name = QLabel("")
        self.lbl_server = QLabel("")
        self.lbl_files_count = QLabel("")
        info_layout.addRow("项目名称:", self.lbl_project_name)
        info_layout.addRow("目标服务器:", self.lbl_server)
        info_layout.addRow("配置文件数:", self.lbl_files_count)
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # 操作按钮
        action_group = QGroupBox("操作")
        action_layout = QVBoxLayout()

        # 第一行：完整部署
        row1 = QHBoxLayout()
        self.btn_full_deploy = QPushButton("完整部署（上传文件+部署脚本）")
        self.btn_full_deploy.clicked.connect(self.full_deploy)
        row1.addWidget(self.btn_full_deploy)
        action_layout.addLayout(row1)

        # 第二行：上传操作
        row2 = QHBoxLayout()
        self.btn_run_pre_commands = QPushButton("执行前置命令")
        self.btn_run_pre_commands.clicked.connect(self.run_pre_commands)
        self.btn_upload_files = QPushButton("上传文件")
        self.btn_upload_files.clicked.connect(self.upload_project_files)
        row2.addWidget(self.btn_run_pre_commands)
        row2.addWidget(self.btn_upload_files)
        action_layout.addLayout(row2)

        # 第三行：脚本执行
        row3 = QHBoxLayout()
        self.btn_deploy_script = QPushButton("执行部署脚本")
        self.btn_deploy_script.clicked.connect(lambda: self.execute_script("deploy"))
        self.btn_restart_script = QPushButton("执行重启脚本")
        self.btn_restart_script.clicked.connect(lambda: self.execute_script("restart"))
        self.btn_status_script = QPushButton("执行状态脚本")
        self.btn_status_script.clicked.connect(lambda: self.execute_script("status"))
        row3.addWidget(self.btn_deploy_script)
        row3.addWidget(self.btn_restart_script)
        row3.addWidget(self.btn_status_script)
        action_layout.addLayout(row3)

        action_group.setLayout(action_layout)
        layout.addWidget(action_group)

        # 进度条
        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        # 日志输出
        log_group = QGroupBox("执行日志")
        log_layout = QVBoxLayout()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        log_layout.addWidget(self.log)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        # 底部按钮
        bottom_layout = QHBoxLayout()
        self.btn_stop = QPushButton("⏹ 停止执行")
        self.btn_stop.clicked.connect(self.stop_execution)
        self.btn_stop.setEnabled(False)  # 默认禁用
        self.btn_stop.setStyleSheet("QPushButton { background-color: #d32f2f; color: white; font-weight: bold; }")
        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(self.log.clear)
        self.btn_config = QPushButton("配置管理")
        self.btn_config.clicked.connect(self.open_config_editor)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_stop)
        bottom_layout.addWidget(self.btn_clear_log)
        bottom_layout.addWidget(self.btn_config)
        layout.addLayout(bottom_layout)

        self.load_projects()

    def load_projects(self):
        self.combo_project.clear()
        projects = self.config.get("projects", {})
        for project_id, project_data in projects.items():
            display_name = f"{project_data.get('name', project_id)} ({project_id})"
            self.combo_project.addItem(display_name, project_id)

        if self.combo_project.count() > 0:
            self.on_project_changed(self.combo_project.currentText())

    def on_project_changed(self, text):
        if not text:
            return

        project_id = self.combo_project.currentData()
        if not project_id:
            return

        project_data = self.config["projects"].get(project_id, {})
        
        self.lbl_project_name.setText(project_data.get("name", ""))
        self.lbl_server.setText(project_data.get("server", ""))
        files_count = len(project_data.get("files", []))
        self.lbl_files_count.setText(str(files_count))


    def get_current_project_config(self):
        project_id = self.combo_project.currentData()
        if not project_id:
            QMessageBox.warning(self, "提示", "请先选择项目")
            return None, None

        project_cfg = self.config["projects"].get(project_id)
        if not project_cfg:
            QMessageBox.warning(self, "提示", "项目配置不存在")
            return None, None

        server_name = project_cfg.get("server")
        server_cfg = self.config["servers"].get(server_name)
        if not server_cfg:
            QMessageBox.warning(self, "提示", f"服务器配置不存在: {server_name}")
            return None, None

        return project_cfg, server_cfg

    def full_deploy(self):
        project_cfg, server_cfg = self.get_current_project_config()
        if not project_cfg or not server_cfg:
            return

        files = project_cfg.get("files", [])
        if not files:
            QMessageBox.warning(self, "提示", "项目未配置任何文件")
            return

        total_files = len(files)
        self.progress.setMaximum(total_files if total_files > 0 else 1)
        self.progress.setValue(0)
        self.log.clear()
        
        # 启用停止按钮并重置停止标志
        self.stop_flag['stop'] = False
        self.btn_stop.setEnabled(True)

        t = threading.Thread(
            target=full_deploy_worker,
            args=(server_cfg, project_cfg, self.signals, self.stop_flag),
            daemon=True
        )
        self.current_thread = t
        t.start()

    def run_pre_commands(self):
        """执行前置命令"""
        project_cfg, _ = self.get_current_project_config()
        if not project_cfg:
            return

        pre_commands = project_cfg.get("pre_commands", [])
        if not pre_commands:
            QMessageBox.information(self, "提示", "项目未配置前置命令")
            return

        self.progress.setMaximum(0)  # 不确定进度
        self.progress.setValue(0)
        self.log.clear()
        
        # 启用停止按钮并重置停止标志
        self.stop_flag['stop'] = False
        self.btn_stop.setEnabled(True)

        def worker():
            try:
                if not execute_local_commands(pre_commands, self.signals, self.stop_flag):
                    self.signals.finished.emit(False, "前置命令执行失败")
                else:
                    self.signals.finished.emit(True, "前置命令执行完成")
            except Exception as e:
                self.signals.finished.emit(False, f"执行失败: {str(e)}")

        t = threading.Thread(target=worker, daemon=True)
        self.current_thread = t
        t.start()

    def upload_project_files(self):
        project_cfg, server_cfg = self.get_current_project_config()
        if not project_cfg or not server_cfg:
            return

        files = project_cfg.get("files", [])
        if not files:
            QMessageBox.warning(self, "提示", "项目未配置任何文件")
            return

        total_files = len(files)
        self.progress.setMaximum(total_files if total_files > 0 else 1)
        self.progress.setValue(0)
        self.log.clear()
        
        # 启用停止按钮并重置停止标志
        self.stop_flag['stop'] = False
        self.btn_stop.setEnabled(True)

        t = threading.Thread(
            target=upload_project_files_worker,
            args=(server_cfg, project_cfg, self.signals, self.stop_flag),
            daemon=True
        )
        self.current_thread = t
        t.start()

    def execute_script(self, script_type):
        project_cfg, server_cfg = self.get_current_project_config()
        if not project_cfg or not server_cfg:
            return

        script_cmd = project_cfg.get("scripts", {}).get(script_type, "")
        if not script_cmd:
            QMessageBox.warning(self, "提示", f"{script_type} 脚本未配置")
            return

        self.progress.setMaximum(0)  # 不确定进度
        self.log.clear()
        
        # 启用停止按钮并重置停止标志
        self.stop_flag['stop'] = False
        self.btn_stop.setEnabled(True)

        t = threading.Thread(
            target=execute_script_worker,
            args=(server_cfg, script_cmd, self.signals, self.stop_flag),
            daemon=True
        )
        self.current_thread = t
        t.start()

    def on_progress(self, value):
        if self.progress.maximum() > 0:
            self.progress.setValue(self.progress.value() + value)

    def on_log(self, text):
        if text.strip():
            self.log.append(text)

    def on_finished(self, success, message):
        # 重置进度条（停止滚动）
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        
        # 禁用停止按钮
        self.btn_stop.setEnabled(False)
        self.stop_flag['stop'] = False
        self.current_thread = None
        
        if success:
            QMessageBox.information(self, "成功", message)
        else:
            QMessageBox.critical(self, "失败", message)

    def stop_execution(self):
        """停止当前执行的操作"""
        self.stop_flag['stop'] = True
        self.btn_stop.setEnabled(False)
        self.log.append("\n⚠ 正在停止操作...")
        QMessageBox.information(self, "提示", "已发送停止信号，操作将尽快终止")

    def open_config_editor(self):
        dlg = ConfigEditor(self)
        dlg.exec()
        self.config = load_full_config()
        self.load_projects()


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)
    ensure_config_exists()
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
