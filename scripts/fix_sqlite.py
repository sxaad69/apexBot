import re

file_path = "database/sqlite_manager.py"
with open(file_path, "r") as f:
    content = f.read()

# We will use regex to find blocks of code starting with `conn = self._get_connection(...)` 
# and ending with `conn.close()` inside a `try...except` block, and transform them.

# A safer approach for this specific file is to define a helper function.
def replace_block(text, func_name):
    # Find the function definition
    func_pattern = re.compile(rf'    def {func_name}\(.*?\):\n(?:.*?\n)*?        try:\n', re.MULTILINE)
    match = func_pattern.search(text)
    if not match: return text
    
    start_idx = match.end()
    
    # Find the except block
    except_pattern = re.compile(r'        except Exception as e:\n', re.MULTILINE)
    except_match = except_pattern.search(text, start_idx)
    if not except_match: return text
    
    end_idx = except_match.start()
    
    try_body = text[start_idx:end_idx]
    
    # We expect `conn = self._get_connection(...)` to be the first line
    lines = try_body.split('\n')
    
    new_lines = []
    
    if len(lines) > 0 and 'conn = self._get_connection' in lines[0]:
        new_lines.append(lines[0])
        new_lines.append('            try:')
        
        # Indent the rest and remove conn.close()
        for line in lines[1:]:
            if 'conn.close()' in line:
                continue
            if line == '':
                new_lines.append('')
            else:
                new_lines.append('    ' + line)
                
        # We also need to add the finally block but before the empty strings at the end
        # Actually it's simpler to just append it
        
        # trim trailing empty lines in new_lines temporarily to place finally block correctly
        while new_lines and new_lines[-1].strip() == '':
            new_lines.pop()
            
        new_lines.append('            finally:')
        new_lines.append('                conn.close()')
        new_lines.append('')
        
        new_body = '\n'.join(new_lines)
        return text[:start_idx] + new_body + text[end_idx:]
    
    return text

funcs_to_fix = [
    'log_rejection', 'get_setting', 'set_setting', 'get_trades',
    'record_trade', 'update_trade_metadata', 'close_trade',
    'save_analysis', 'save_signal', 'log_activity',
    'upsert_metrics', 'purge_old_activity'
]

for func in funcs_to_fix:
    content = replace_block(content, func)

with open(file_path, "w") as f:
    f.write(content)
print("Finished patching sqlite_manager.py")
