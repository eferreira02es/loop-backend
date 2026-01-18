
with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    for i, line in enumerate(lines):
        if 'timedelta' in line:
            print(f"{i+1}: {line.strip()}")
