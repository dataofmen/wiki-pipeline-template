import os
import re
import json

def fix_title(match):
    raw_title = match.group(1).strip()
    
    if raw_title.startswith('"') and raw_title.endswith('"'):
        # It might be properly escaped, or not.
        content = raw_title[1:-1].replace('\\"', '"')
    elif raw_title.startswith("'") and raw_title.endswith("'"):
        content = raw_title[1:-1].replace("''", "'")
    else:
        content = raw_title
    
    # Safe YAML string representation (YAML is a superset of JSON)
    safe_yaml_title = json.dumps(content, ensure_ascii=False)
    return f"title: {safe_yaml_title}"

def fix_tags(match):
    tags_inner = match.group(1)
    # Split by comma, strip spaces, replace internal spaces with hyphens
    tags_list = [t.strip().replace(' ', '-') for t in tags_inner.split(',')]
    fixed_tags = ', '.join(tags_list)
    return f"tags: [{fixed_tags}]"

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # The frontmatter is between the first two `---`
    parts = content.split('---', 2)
    if len(parts) >= 3 and parts[0].strip() == '':
        frontmatter = parts[1]
        
        # Replace title line safely
        new_frontmatter = re.sub(r'^title:\s*(.*)$', fix_title, frontmatter, flags=re.MULTILINE)
        
        # Replace tags line (spaces to hyphens)
        new_frontmatter = re.sub(r'^tags:\s*\[(.*?)\]$', fix_tags, new_frontmatter, flags=re.MULTILINE)
        
        if new_frontmatter != frontmatter:
            parts[1] = new_frontmatter
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('---'.join(parts))
            print(f"Fixed title in: {filepath}")

def sanitize_markdown_content(content):
    parts = content.split('---', 2)
    if len(parts) >= 3 and parts[0].strip() == '':
        frontmatter = parts[1]
        new_frontmatter = re.sub(r'^title:\s*(.*)$', fix_title, frontmatter, flags=re.MULTILINE)
        new_frontmatter = re.sub(r'^tags:\s*\[(.*?)\]$', fix_tags, new_frontmatter, flags=re.MULTILINE)
        if new_frontmatter != frontmatter:
            parts[1] = new_frontmatter
            return '---'.join(parts)
    return content

def main():
    dirs = ['/Users/hmkwon/Projects/0004_Wikis/wiki', '/Users/hmkwon/Projects/0004_Wikis/garden']
    for d in dirs:
        for root, _, files in os.walk(d):
            for file in files:
                if file.endswith('.md'):
                    fix_file(os.path.join(root, file))

if __name__ == "__main__":
    main()
