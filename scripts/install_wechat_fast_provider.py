"""Install the reviewed signed compatibility provider for the fast WeChat model."""
from pathlib import Path
from install_xinference_provider import main, validate_package

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'evidence/20261023/openai_compatible_provider'
CONFIG = {
    'plugin_id': 'langgenius/openai_api_compatible', 'version': '0.0.20',
    'identifier': 'langgenius/openai_api_compatible:0.0.20@33f67ff4e0b5b92f6c7889269fefe777e37421d0d314b3e1fbb1bb65b5afc59f',
    'sha256': 'abeac63d6de14d6511a983a5b75844853eb8525012547b6045231d932283ebc3',
    'package': FOLDER / 'openai_api_compatible-0.0.20.difypkg',
    'report': FOLDER / 'installation.json',
    'provider': 'langgenius/openai_api_compatible/openai_api_compatible',
    'marketplace_versions': FOLDER / 'rawmarketplaceversions.json',
    'required_model_types': ('llm',), 'sdk_requirement': 'dify-plugin==0.4.3',
}

if __name__ == '__main__':
    validate_package(CONFIG['package'].read_bytes(), CONFIG)
    main(CONFIG)
