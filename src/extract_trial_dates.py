#!/usr/bin/env python3
"""
Script to extract trial/judgment dates from HTML files and update JSON files.
Only extracts dates that are actually present in the HTML - no hallucinations.
"""

import json
import os
import re
from pathlib import Path
from html.parser import HTMLParser
from html import unescape
from typing import Optional, List
from datetime import datetime

class DateExtractor(HTMLParser):
    """HTML parser to extract text from first 150 lines for date extraction."""
    def __init__(self):
        super().__init__()
        self.text_lines = []
        self.in_body = False
        self.line_count = 0
        self.max_lines = 150
        
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'body':
            self.in_body = True
            
    def handle_endtag(self, tag):
        if tag.lower() == 'body':
            self.in_body = False
            
    def handle_data(self, data):
        if self.in_body and self.line_count < self.max_lines:
            lines = data.split('\n')
            for line in lines:
                if self.line_count >= self.max_lines:
                    break
                stripped = line.strip()
                if stripped:
                    self.text_lines.append(stripped)
                    self.line_count += 1

def extract_text_first_150_lines(html_content: str) -> List[str]:
    """Extract plain text from first 150 lines of HTML body."""
    parser = DateExtractor()
    try:
        parser.feed(html_content)
        return parser.text_lines[:150]
    except Exception as e:
        print(f"Error extracting text: {e}")
        return []

def parse_date_from_text(date_str: str) -> Optional[str]:
    """Parse a date string and return in ISO format (YYYY-MM-DD) or original format if parsing fails."""
    date_str = date_str.strip()
    
    # Try to parse various date formats
    date_formats = [
        # Written formats: "26th March, 2004" or "26 March 2004" or "March 26, 2004"
        (r'(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December),?\s+(\d{4})', '%d %B %Y'),
        (r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', '%B %d %Y'),
        # Uppercase formats: "15TH NOVEMBER, 2006" or "15TH NOVEMBER 2006"
        (r'(\d{1,2})(?:ST|ND|RD|TH)?\s+(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER),?\s+(\d{4})', '%d %B %Y'),
        # DD/MM/YYYY or MM/DD/YYYY
        (r'(\d{1,2})/(\d{1,2})/(\d{4})', None),  # Special handling
        # YYYY-MM-DD
        (r'(\d{4})-(\d{1,2})-(\d{1,2})', '%Y-%m-%d'),
    ]
    
    for pattern, fmt in date_formats:
        match = re.search(pattern, date_str, re.IGNORECASE)
        if match:
            if fmt is None:  # DD/MM/YYYY format
                day, month, year = match.groups()
                try:
                    # Try DD/MM/YYYY first (common in Ghana)
                    dt = datetime(int(year), int(month), int(day))
                    return dt.strftime('%Y-%m-%d')
                except ValueError:
                    # Try MM/DD/YYYY
                    try:
                        dt = datetime(int(year), int(day), int(month))
                        return dt.strftime('%Y-%m-%d')
                    except ValueError:
                        continue
            else:
                try:
                    # Clean up the date string
                    clean_date = match.group(0)
                    # Remove ordinal suffixes (case insensitive)
                    clean_date = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', clean_date, flags=re.IGNORECASE)
                    # Remove commas
                    clean_date = clean_date.replace(',', '')
                    # Convert to title case for month names if needed
                    clean_date = clean_date.title()
                    dt = datetime.strptime(clean_date.strip(), fmt)
                    return dt.strftime('%Y-%m-%d')
                except (ValueError, AttributeError):
                    continue
    
    # If we can't parse it, return the original (cleaned up)
    return date_str.strip()

def find_judgment_date_in_html(html_content: str) -> Optional[str]:
    """Find judgment date in HTML content."""
    # First, try to extract dates directly from HTML (before removing tags)
    # This helps catch dates split across HTML tags like <u>15<sup>TH</sup> NOVEMBER, 2006</u>
    
    # Remove script and style tags first
    html_clean = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    html_clean = re.sub(r'<style[^>]*>.*?</style>', '', html_clean, flags=re.DOTALL | re.IGNORECASE)
    
    # Look for dates in HTML (handles split tags)
    html_date_patterns = [
        # Pattern for dates with superscript: <u>15<sup>TH</sup> NOVEMBER, 2006</u>
        r'<[^>]*>(\d{1,2})<[^>]*>(?:ST|ND|RD|TH)<[^>]*>\s*(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER),?\s*(\d{4})',
        # Pattern for dates in underlined sections
        r'<u[^>]*>(\d{1,2}(?:ST|ND|RD|TH)?\s+(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER),?\s+\d{4})',
    ]
    
    for pattern in html_date_patterns:
        match = re.search(pattern, html_clean, re.IGNORECASE)
        if match:
            if len(match.groups()) == 3:  # Date with superscript
                day, month, year = match.groups()
                try:
                    month_map = {
                        'JANUARY': 'January', 'FEBRUARY': 'February', 'MARCH': 'March',
                        'APRIL': 'April', 'MAY': 'May', 'JUNE': 'June',
                        'JULY': 'July', 'AUGUST': 'August', 'SEPTEMBER': 'September',
                        'OCTOBER': 'October', 'NOVEMBER': 'November', 'DECEMBER': 'December'
                    }
                    month_name = month_map.get(month.upper(), month.title())
                    dt = datetime(int(year), datetime.strptime(month_name, '%B').month, int(day))
                    return dt.strftime('%Y-%m-%d')
                except (ValueError, KeyError):
                    continue
            else:  # Full date match
                date_str = match.group(1)
                parsed = parse_date_from_text(date_str)
                if parsed and len(parsed) >= 10:
                    return parsed
    
    # Extract text from first 150 lines (header area)
    lines = extract_text_first_150_lines(html_content)
    if not lines:
        return None
    
    # Combine lines for pattern matching
    header_text = ' '.join(lines[:50])  # First 50 lines should contain the date
    
    # Pattern 1: Date in brackets with case title: [26/03/2004] or [26 March 2004]
    bracket_patterns = [
        r'\[(\d{1,2}/\d{1,2}/\d{4})\]',  # [26/03/2004]
        r'\[(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4})\]',  # [26th March, 2004]
    ]
    
    for pattern in bracket_patterns:
        match = re.search(pattern, header_text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            parsed = parse_date_from_text(date_str)
            if parsed and len(parsed) >= 10:  # Valid date format
                return parsed
    
    # Pattern 2: Written date format near case number or after "Coram"
    # Look for dates that appear after case numbers or near "Coram" or "JUDGMENT"
    written_date_patterns = [
        r'(?:H\d+/\d+/\d+|NO\.?\s*[A-Z]?\.?\d+/\d+|J\.\d+/\d+)[^.]*?(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4})',
        r'(?:Coram|CORAM)[^.]*?(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4})',
        r'(?:JUDGMENT|Judgment)[^.]*?(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4})',
        # Pattern for uppercase dates: "15TH NOVEMBER, 2006"
        r'(\d{1,2}(?:ST|ND|RD|TH)?\s+(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER),?\s+\d{4})',
    ]
    
    for pattern in written_date_patterns:
        match = re.search(pattern, header_text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            parsed = parse_date_from_text(date_str)
            if parsed and len(parsed) >= 10:
                return parsed
    
    # Pattern 3: Standalone written date in header (uppercase or title case)
    # Look for dates that are on their own line or clearly separated
    standalone_patterns = [
        r'^(\d{1,2}(?:ST|ND|RD|TH)?\s+(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER),?\s+\d{4})',
        r'^(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4})',
        # Also match dates that might be in underlined/bold sections
        r'<u>(\d{1,2}(?:ST|ND|RD|TH)?\s+(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER),?\s+\d{4})',
    ]
    
    for line in lines[:30]:  # Check first 30 lines
        for pattern in standalone_patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                # Skip if it's clearly part of a sentence or contains other words
                if any(word in line.upper() for word in ['FROM', 'TO', 'ON', 'AT', 'BEFORE', 'AFTER', 'DURING']):
                    continue
                parsed = parse_date_from_text(date_str)
                if parsed and len(parsed) >= 10:
                    return parsed
    
    # Pattern 4: Date in DD/MM/YYYY format near case title (not in brackets)
    # Only if it appears very early in the document
    early_date_pattern = r'(\d{1,2}/\d{1,2}/\d{4})'
    for i, line in enumerate(lines[:20]):  # First 20 lines only
        match = re.search(early_date_pattern, line)
        if match:
            date_str = match.group(1)
            # Check if it's near case title or case number
            context = ' '.join(lines[max(0, i-2):i+3])
            if any(word in context.upper() for word in ['V.', 'VRS', 'VERSUS', 'H1/', 'NO.', 'CASE']):
                parsed = parse_date_from_text(date_str)
                if parsed and len(parsed) >= 10:
                    return parsed
    
    return None

def extract_trial_date(html_path: str) -> Optional[str]:
    """Extract trial/judgment date from HTML file."""
    if not os.path.exists(html_path):
        return None
    
    try:
        with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
            html_content = f.read()
        
        date = find_judgment_date_in_html(html_content)
        return date
    except Exception as e:
        print(f"Error reading HTML file {html_path}: {e}")
        return None

def update_json_file(json_path: str, law_finder_path: str) -> bool:
    """Update a single JSON file with extracted trial date."""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Skip if already has a date
        if 'trialDate' in data and data['trialDate']:
            return False
        
        # Get source path from metadata
        source_path = data.get('metadata', {}).get('sourcePath', '')
        if not source_path:
            return False
        
        # Construct full HTML path
        html_path = os.path.join(law_finder_path, source_path)
        
        # Extract date
        date = extract_trial_date(html_path)
        
        if date:
            data['trialDate'] = date
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
    
    print(f"Processing {total} JSON files to extract trial dates...")
    
    for i, json_file in enumerate(json_files, 1):
        if i % 100 == 0:
            print(f"Processed {i}/{total} files... (Updated: {updated}, Failed: {failed})")
        
        if update_json_file(str(json_file), str(law_finder_dir)):
            updated += 1
        else:
            failed += 1
    
    print(f"\nCompleted!")
    print(f"Updated: {updated}")
    print(f"Failed/No date found: {failed}")
    print(f"Total: {total}")

if __name__ == '__main__':
    main()

