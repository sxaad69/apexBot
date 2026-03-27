import re

with open('database/sqlite_manager.py', 'r') as f:
    lines = f.readlines()

new_lines = []
in_try_block = False
in_funcs = [
    'log_rejection', 'get_setting', 'set_setting', 'get_trades',
    'record_trade', 'update_trade_metadata', 'close_trade',
    'save_analysis', 'save_signal', 'log_activity',
    'upsert_metrics', 'purge_old_activity'
]

# We will use a simpler approach. Just read the file and replace.
text = "".join(lines)

def replacer(match):
    # match.group(0) is the whole body
    # we want to wrap the body in try/finally
    prefix = match.group(1) # try:\n            conn = self._get_connection...
    conn_line = match.group(2)
    body = match.group(3)
    
    # remove conn.close() from body
    body_no_close = re.sub(r'^[ \t]*conn\.close\(\)\n', '', body, flags=re.MULTILINE)
    
    indent = "            "
    
    new_block = f"try:\n{indent}{conn_line}\n{indent}try:\n"
    
    # indent body by 4 spaces
    indented_body = ""
    for line in body_no_close.split('\n'):
        if line.strip():
            indented_body += "    " + line + "\n"
        else:
            indented_body += "\n"
            
    new_block += indented_body
    new_block += f"{indent}finally:\n{indent}    if 'conn' in locals():\n{indent}        conn.close()\n"
    return new_block

# A bit fragile. Let's make an explicit replacer script tailored to the file instead of full regex.
