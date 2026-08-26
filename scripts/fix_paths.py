# DEPRECATED (US-005): 기능이 scripts/normalize.py 로 흡수됨. 신규 코드는 normalize.py 를 사용할 것.
import os
import shutil
import re
from pathlib import Path

def get_frontmatter(content):
    parts = content.split('---', 2)
    if len(parts) >= 3 and parts[0].strip() == '':
        return parts[1]
    return ""

def parse_yaml(yaml_text, key):
    match = re.search(rf"^{key}:\s*(.+)$", yaml_text, re.MULTILINE)
    if match:
        return match.group(1).strip().strip('"\'')
    return None

def fix_all_paths(root_dir):
    root = Path(root_dir)
    
    # Files to process: everything in garden/ and wiki/ (excluding index.md etc)
    for folder in ["garden", "wiki"]:
        dir_path = root / folder
        if not dir_path.exists():
            continue
            
        for file_path in dir_path.rglob("*.md"):
            if file_path.name == "index.md":
                continue
            # `_quarantine/` 은 의도적으로 격리한 노트다. type/target 기준으로
            # 원위치로 되돌리면 격리가 무력화된다 (실제로 격리한 노트 14개가
            # 이 루프에 의해 wiki/concepts·garden 으로 복귀한 사고가 있었다).
            if "_quarantine" in file_path.parts:
                continue
                
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            fm = get_frontmatter(content)
            if not fm:
                continue
                
            target = parse_yaml(fm, "target")
            type_val = parse_yaml(fm, "type")
            
            # Determine correct directory.
            # Public /wiki has exactly two child folders: concepts/ and entities/.
            # Any legacy wiki/*.md or wiki/<other-folder>/*.md file is normalized
            # into one of those two buckets so sync_content.sh never publishes
            # extra wiki sections such as /wiki/events/.
            if target == "human":
                correct_dir = root / "garden"
            elif target == "machine" or folder == "wiki":
                if type_val and type_val.startswith("entity"):
                    correct_dir = root / "wiki" / "entities"
                else:
                    # concept is the default for every non-entity wiki note,
                    # including legacy notes that predate target/type fields.
                    correct_dir = root / "wiki" / "concepts"
            else:
                continue # Unknown
                
            correct_dir.mkdir(parents=True, exist_ok=True)
            correct_path = correct_dir / file_path.name
            
            # If the file is not where it belongs, move it
            if file_path.resolve() != correct_path.resolve():
                print(f"Moving {file_path.relative_to(root)} -> {correct_path.relative_to(root)}")
                
                # Check for conflict
                if correct_path.exists():
                    correct_path.unlink() # Overwrite with the latest one, or just replace
                    
                shutil.move(str(file_path), str(correct_path))

    # Clean up empty directories
    for folder in ["garden", "wiki"]:
        dir_path = root / folder
        if not dir_path.exists():
            continue
        for root_p, dirs, files in os.walk(dir_path, topdown=False):
            # Do not delete the main folders
            if Path(root_p) in [root / "garden", root / "wiki", root / "wiki" / "concepts", root / "wiki" / "entities"]:
                continue
            # 격리 디렉터리는 비어 있어도 지우지 않는다.
            if "_quarantine" in Path(root_p).parts:
                continue
            if not os.listdir(root_p):
                print(f"Removing empty directory: {Path(root_p).relative_to(root)}")
                os.rmdir(root_p)

if __name__ == "__main__":
    import sys
    # Project root is the parent of the scripts folder
    root_dir = Path(__file__).parent.parent
    fix_all_paths(root_dir)
