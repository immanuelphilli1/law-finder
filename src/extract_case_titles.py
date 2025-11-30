#!/usr/bin/env python3
"""
Script to extract case titles from HTML files and update JSON files.
Extracts titles from plain text body (first 50 lines), falls back to HTML parsing,
and uses filename as last resort.
"""

import json
import os
import re
from pathlib import Path
from html.parser import HTMLParser
from html import unescape
from typing import Optional, Tuple

class TextExtractor(HTMLParser):
    """HTML parser to extract plain text."""
    def __init__(self):
        super().__init__()
        self.text = []
        self.in_body = False
        self.line_count = 0
        self.max_lines = 50
        
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'body':
            self.in_body = True
            
    def handle_endtag(self, tag):
        if tag.lower() == 'body':
            self.in_body = False
            
    def handle_data(self, data):
        if self.in_body and self.line_count < self.max_lines:
            # Split by newlines and add each line
            lines = data.split('\n')
            for line in lines:
                if self.line_count >= self.max_lines:
                    break
                stripped = line.strip()
                if stripped:
                    self.text.append(stripped)
                    self.line_count += 1

def extract_plain_text_first_50_lines(html_content: str) -> str:
    """Extract plain text from first 50 lines of HTML body."""
    parser = TextExtractor()
    try:
        parser.feed(html_content)
        return '\n'.join(parser.text[:50])
    except Exception as e:
        print(f"Error extracting plain text: {e}")
        return ""

def find_case_title_in_text(text: str) -> Optional[str]:
    """Find case title pattern in plain text."""
    # Clean up text - remove HTML entities
    text = unescape(text)
    
    # Split into lines for better matching
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    # Strategy 1: Look for complete title on a single line
    single_line_patterns = [
        # Pattern: PLAINTIFF v. DEFENDANT [optional date/citation]
        r'^([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?(?:\s+ETC\.?)?)\s+v\.?\s+([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?(?:\s+ETC\.?)?)(?:\s+\[.*?\])?',
        # Pattern: PLAINTIFF VRS DEFENDANT
        r'^([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?(?:\s+ETC\.?)?)\s+VRS\.?\s+([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?(?:\s+ETC\.?)?)',
        # Pattern: THE REPUBLIC v. DEFENDANT
        r'^(THE\s+REPUBLIC)\s+v\.?\s+([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?(?:\s+ETC\.?)?)',
    ]
    
    for line in lines[:30]:  # Check first 30 lines
        for pattern in single_line_patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                title = match.group(0).strip()
                # Clean up
                title = re.sub(r'\s*\[.*?\]\s*$', '', title)
                title = re.sub(r'\s*H\d+/\d+/\d+.*?$', '', title)
                title = re.sub(r'\s+', ' ', title)
                title = title.replace('&amp;', '&')
                if len(title) > 10 and 'pages.gif' not in title.lower():
                    return title
    
    # Strategy 2: Look for split titles (PLAINTIFF on one line, VRS/VERSUS/v. on another, DEFENDANT on third)
    for i in range(min(20, len(lines) - 2)):
        line1 = lines[i].upper()
        line2 = lines[i + 1].upper() if i + 1 < len(lines) else ""
        line3 = lines[i + 2].upper() if i + 2 < len(lines) else ""
        
        # Check if line2 contains v./vrs/versus
        if re.search(r'\b(V\.?|VRS\.?|VERSUS)\b', line2):
            # Extract plaintiff from line1, defendant from line3
            plaintiff_match = re.match(r'^([A-Z][A-Z\s&.,\'\-\d()]+)', lines[i])
            defendant_match = re.match(r'^([A-Z][A-Z\s&.,\'\-\d()]+)', lines[i + 2])
            
            if plaintiff_match and defendant_match:
                plaintiff = plaintiff_match.group(1).strip()
                defendant = defendant_match.group(1).strip()
                
                # Clean up
                plaintiff = re.sub(r'\s+', ' ', plaintiff)
                defendant = re.sub(r'\s+', ' ', defendant)
                
                # Skip if contains metadata words
                if any(word in plaintiff.upper() for word in ['PLAINTIFF', 'RESPONDENT', 'APPELLANT', 'CORAM', 'JUDGMENT']):
                    continue
                if any(word in defendant.upper() for word in ['DEFENDANT', 'RESPONDENT', 'APPELLANT']):
                    continue
                
                # Construct title
                v_word = 'v.' if 'V\.' in line2 or 'V ' in line2 else 'VRS' if 'VRS' in line2 else 'VERSUS'
                title = f"{plaintiff} {v_word} {defendant}"
                title = title.replace('&amp;', '&')
                
                if len(title) > 10:
                    return title
    
    # Strategy 3: Look for "IN THE MATTER OF" pattern
    matter_pattern = r'(IN\s+THE\s+MATTER\s+OF[^\.]+)'
    full_text = ' '.join(lines[:30])
    match = re.search(matter_pattern, full_text, re.IGNORECASE)
    if match:
        title = match.group(1).strip()
        title = re.sub(r'\s+', ' ', title)
        if len(title) > 10:
            return title
    
    return None

def extract_title_from_html(html_content: str) -> Optional[str]:
    """Extract case title from HTML using more sophisticated parsing."""
    # Try to find title in HTML - look for patterns in text content
    # Remove script and style tags first
    html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    
    # Look for case title patterns in HTML text
    patterns = [
        # Pattern in HTML: PLAINTIFF v. DEFENDANT
        r'([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?)\s+v\.?\s+([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?)',
        # Pattern: PLAINTIFF VRS DEFENDANT
        r'([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?)\s+VRS\.?\s+([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?)',
        # Pattern: THE REPUBLIC v. DEFENDANT
        r'(THE\s+REPUBLIC)\s+v\.?\s+([A-Z][A-Z\s&.,\'\-\d()]+(?:\s+&(?:\s+ANO?\.?)?)?(?:\s+&(?:\s+ORS?\.?)?)?)',
    ]
    
    # Extract text from first part of HTML (first 5000 chars should contain title)
    html_snippet = html_content[:5000]
    
    for pattern in patterns:
        matches = re.finditer(pattern, html_snippet, re.IGNORECASE)
        for match in matches:
            title = match.group(0)
            # Remove HTML tags
            title = re.sub(r'<[^>]+>', '', title)
            title = unescape(title)
            title = re.sub(r'\s+', ' ', title).strip()
            # Remove trailing metadata
            title = re.sub(r'\s*\[.*?\]\s*$', '', title)
            title = re.sub(r'\s*H\d+/\d+/\d+.*?$', '', title)
            title = title.replace('&amp;', '&')
            if len(title) > 10 and 'pages.gif' not in title.lower():
                return title
    
    return None

def extract_title_from_filename(filename: str) -> Optional[str]:
    """Extract case title from JSON filename."""
    # Remove the JSON extension and path prefixes
    base = os.path.basename(filename)
    base = base.replace('.json', '')
    
    # Remove common prefixes
    prefixes = [
        'COURT OF APPEAL__',
        'SUPREME COURT__',
        'WACA__',
        'WALR__',
    ]
    
    for prefix in prefixes:
        if base.startswith(prefix):
            base = base[len(prefix):]
            # Remove additional path parts (like "supreme court rep cases (1)__2006A__")
            parts = base.split('__')
            # Take the last meaningful part (usually the case title)
            if len(parts) > 1:
                # Skip numeric years and folder names
                for part in reversed(parts):
                    if part and not part.isdigit() and not part.startswith('20') and part != 'TEMP':
                        base = part
                        break
    
    # Clean up the title
    base = base.replace('__', ' ')
    base = re.sub(r'\s+', ' ', base)
    
    if len(base) > 10 and base != 'pages.gif':
        return base.strip()
    
    return None

def extract_case_title(html_path: str, json_filename: str) -> Optional[str]:
    """Extract case title using multiple strategies."""
    # Strategy 1: Extract from plain text (first 50 lines)
    if os.path.exists(html_path):
        try:
            with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
                html_content = f.read()
            
            # Extract plain text from first 50 lines
            plain_text = extract_plain_text_first_50_lines(html_content)
            if plain_text:
                title = find_case_title_in_text(plain_text)
                if title and title != 'pages.gif':
                    return title
            
            # Strategy 2: HTML parsing
            title = extract_title_from_html(html_content)
            if title and title != 'pages.gif':
                return title
        except Exception as e:
            print(f"Error reading HTML file {html_path}: {e}")
    
    # Strategy 3: Use filename as last resort
    title = extract_title_from_filename(json_filename)
    if title and title != 'pages.gif':
        return title
    
    return None

def update_json_file(json_path: str, law_finder_path: str) -> bool:
    """Update a single JSON file with extracted case title."""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Skip if already has a valid title
        if data.get('caseTitle') and data['caseTitle'] != 'pages.gif':
            return False
        
        # Get source path from metadata
        source_path = data.get('metadata', {}).get('sourcePath', '')
        if not source_path:
            # Try filename as fallback
            title = extract_title_from_filename(json_path)
            if title:
                data['caseTitle'] = title
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                return True
            return False
        
        # Construct full HTML path
        html_path = os.path.join(law_finder_path, source_path)
        
        # Extract title
        title = extract_case_title(html_path, json_path)
        
        if title:
            data['caseTitle'] = title
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        
        return False
    except Exception as e:
        print(f"Error processing {json_path}: {e}")
        return False

def main():
    """Main function to process all JSON files."""
    # Get paths
    script_dir = Path(__file__).parent
    json_dir = script_dir / 'law-finder-json'
    law_finder_dir = script_dir / 'LAW FINDER'
    
    if not json_dir.exists():
        print(f"Error: {json_dir} does not exist")
        return
    
    if not law_finder_dir.exists():
        print(f"Error: {law_finder_dir} does not exist")
        return
    
    # Get all JSON files
    json_files = list(json_dir.glob('*.json'))
    total = len(json_files)
    updated = 0
    failed = 0
    
    print(f"Processing {total} JSON files...")
    
    for i, json_file in enumerate(json_files, 1):
        if i % 100 == 0:
            print(f"Processed {i}/{total} files...")
        
        if update_json_file(str(json_file), str(law_finder_dir)):
            updated += 1
        else:
            failed += 1
    
    print(f"\nCompleted!")
    print(f"Updated: {updated}")
    print(f"Failed/Skipped: {failed}")
    print(f"Total: {total}")

if __name__ == '__main__':
    main()

