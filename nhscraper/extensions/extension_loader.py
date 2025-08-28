# extensions/extension_loader.py
import os
import importlib
from core.logger import logger

INSTALLED_EXTENSIONS = []

EXTENSIONS_DIR = os.path.join(os.path.dirname(__file__))
for folder in os.listdir(EXTENSIONS_DIR):
    folder_path = os.path.join(EXTENSIONS_DIR, folder)
    if os.path.isdir(folder_path) and "__init__.py" in os.listdir(folder_path):
        module_name = f"extensions.{folder}.{folder}__nhsext"
        try:
            module = importlib.import_module(module_name)
            INSTALLED_EXTENSIONS.append(module)
            logger.info(f"[+] Loaded extension: {folder}")
        except Exception as e:
            logger.warning(f"[!] Failed to load extension {folder}: {e}")