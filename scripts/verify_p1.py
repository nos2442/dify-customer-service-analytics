"""P01 Windows 依赖及 Notebook 检查，不代表数据库或业务验收通过。"""
import json
import platform
import sys
from datetime import datetime
from importlib import import_module, metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = {
    "numpy": "1.24.4", "pandas": "2.2.3", "matplotlib": "3.5.2",
    "wordcloud": "1.9.4", "scikit-learn": "1.7.2", "fastmcp": "2.13.1",
}
MODULES = {
    "numpy": "numpy", "pandas": "pandas", "matplotlib": "matplotlib",
    "wordcloud": "wordcloud", "scikit-learn": "sklearn", "fastmcp": "fastmcp",
    "PyMySQL": "pymysql", "Flask": "flask", "wechatpy": "wechatpy",
    "gensim": "gensim", "pyLDAvis": "pyLDAvis", "tqdm": "tqdm",
    "jieba": "jieba", "snownlp": "snownlp", "jupyterlab": "jupyterlab",
    "ipykernel": "ipykernel",
}


def main():
    evidence = ROOT / "evidence" / datetime.now().strftime("%Y%m%d")
    evidence.mkdir(parents=True, exist_ok=True)
    report = {"checked_at": datetime.now().astimezone().isoformat(),
              "python": platform.python_version(), "executable": sys.executable,
              "packages": {}, "scope": "P1本地依赖和Notebook；不含P2数据库及业务联调"}
    problems = []
    if platform.python_version() != "3.10.9":
        problems.append("Python版本不符合3.10.9基线")
    for package, module in MODULES.items():
        try:
            import_module(module)
            version = metadata.version(package)
            report["packages"][package] = version
            if package in PINNED and PINNED[package] != version:
                problems.append(f"{package}期望{PINNED[package]}，实际{version}")
        except Exception as exc:
            problems.append(f"{package}: {type(exc).__name__}: {exc}")
    if not problems:
        import nbformat
        from nbclient import NotebookClient
        notebook = nbformat.v4.new_notebook(cells=[
            nbformat.v4.new_markdown_cell(
                "# P01 本地环境验证\n仅用三条明确的计算样例检查内核，不是课程业务数据或分析成果。"),
            nbformat.v4.new_code_cell(
                "import sys\nimport numpy as np\nimport pandas as pd\n"
                "from gensim import corpora, models\nfrom snownlp import SnowNLP\n"
                "from sklearn.decomposition import LatentDirichletAllocation\n"
                "assert sys.version_info[:3] == (3, 10, 9)\n"
                "values = pd.Series([1, 2, 3])\nassert values.sum() == 6\n"
                "topics = LatentDirichletAllocation(n_components=2, random_state=0).fit_transform([[2, 1, 0], [0, 1, 2]])\n"
                "assert topics.shape == (2, 2) and np.allclose(topics.sum(axis=1), 1)\n"
                "print('Python:', sys.version.split()[0])\n"
                "print('numpy:', np.__version__, 'pandas:', pd.__version__)\n"
                "print('Notebook kernel and arithmetic check: PASS')")],
            metadata={"kernelspec": {"display_name": "Dify Project Python 3.10.9",
                                     "language": "python", "name": "dify-project-3109"}})
        notebook_path = evidence / "p01_environment_check.ipynb"
        try:
            NotebookClient(notebook, timeout=120, kernel_name="dify-project-3109").execute()
            report["notebook"] = {"status": "passed", "path": str(notebook_path)}
        except Exception as exc:
            problems.append(f"Notebook: {type(exc).__name__}: {exc}")
            report["notebook"] = {"status": "failed", "path": str(notebook_path)}
        nbformat.write(notebook, notebook_path)
    report["problems"] = problems
    report["status"] = "passed" if not problems else "failed"
    output_path = evidence / "p01_windows_verification.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
