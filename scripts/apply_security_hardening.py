from pathlib import Path

html_path = Path("templates/index.html")
html = html_path.read_text(encoding="utf-8")
old_html = """      tr.innerHTML = `
        <td>${label}</td>
        <td>${value}</td>
      `;
"""
new_html = """      const labelCell = document.createElement('td');
      labelCell.textContent = String(label);
      const valueCell = document.createElement('td');
      valueCell.textContent = String(value);
      tr.append(labelCell, valueCell);
"""
if old_html not in html:
    raise SystemExit("Expected summary-table innerHTML block was not found")
html_path.write_text(html.replace(old_html, new_html, 1), encoding="utf-8")

config_path = Path("config.py")
config = config_path.read_text(encoding="utf-8")
old_save = """        with open(_CREDS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
"""
new_save = """        safe_data = {key: value for key, value in data.items() if key != "password"}
        with open(_CREDS_FILE, "w", encoding="utf-8") as f:
            json.dump(safe_data, f, indent=2)
"""
if old_save not in config:
    raise SystemExit("Expected credential file writer was not found")
config = config.replace(old_save, new_save, 1)

old_env = '    _write_env_var("SF_PASSWORD", password)\n'
new_env = "    # Passwords remain in the OS keyring or current process; never persist them to .env.\n"
if old_env not in config:
    raise SystemExit("Expected SF_PASSWORD .env persistence was not found")
config = config.replace(old_env, new_env, 1)
config_path.write_text(config, encoding="utf-8")
