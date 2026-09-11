"""
项目配置管理

管理测试账号等配置信息，存储在项目根目录的 aegis_config.json
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ConfigManager:
    """项目配置管理器"""

    CONFIG_FILE = "aegis_config.json"

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.config_path = self.project_root / self.CONFIG_FILE
        self._config: Optional[dict] = None

    def config_exists(self) -> bool:
        """检查配置文件是否存在"""
        return self.config_path.exists()

    def get_config(self) -> dict:
        """获取项目配置"""
        if not self.config_exists():
            return None

        if self._config is None:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)
            except Exception as e:
                logger.error(f"读取配置文件失败: {e}")
                return None

        return self._config

    def save_config(self, config: dict) -> bool:
        """保存项目配置"""
        try:
            # 确保目录存在
            self.project_root.mkdir(parents=True, exist_ok=True)

            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            self._config = config
            logger.info(f"配置已保存到: {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    def get_credentials(self, account_type: str = "default") -> Optional[dict]:
        """获取指定类型的账号信息"""
        config = self.get_config()
        if not config:
            return None

        accounts = config.get("accounts", {})
        return accounts.get(account_type)

    def update_credentials(self, account_type: str, username: str, password: str, description: str = "") -> bool:
        """更新指定类型的账号信息"""
        config = self.get_config()
        if config is None:
            config = {
                "project_name": "Test Project",
                "accounts": {}
            }

        if "accounts" not in config:
            config["accounts"] = {}

        config["accounts"][account_type] = {
            "username": username,
            "password": password,
            "description": description
        }

        return self.save_config(config)

    def get_account_types(self) -> list:
        """获取所有账号类型"""
        config = self.get_config()
        if not config:
            return []

        return list(config.get("accounts", {}).keys())


def get_config_manager(project_root: str) -> ConfigManager:
    """获取配置管理器实例"""
    return ConfigManager(project_root)
