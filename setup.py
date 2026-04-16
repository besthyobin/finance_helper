import os
import shutil
import urllib.request
import urllib.parse
import zipfile
import ssl

def setup_project():
    # 1. Cleanup old directories
    for dir_name in ['backend', 'frontend']:
        if os.path.exists(dir_name):
            print(f"Removing {dir_name}...")
            shutil.rmtree(dir_name)

    # 2. Construct URL properly
    base_url = "https://start.spring.io/starter.zip"
    params = {
        'type': 'maven-project',
        'language': 'java',
        'baseDir': 'finance_helper',
        'groupId': 'com.example',
        'artifactId': 'finance_helper',
        'name': 'finance_helper',
        'description': 'Stock Analysis App',
        'packageName': 'com.example.finance_helper',
        'packaging': 'war',
        'javaVersion': '21',
        'dependencies': 'web,data-jpa,postgresql,lombok'
        # Omitted bootVersion to get default stable
    }
    
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    zip_path = "finance_helper.zip"
    
    print(f"Downloading from {url}...")
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        req = urllib.request.Request(url)
        # Adding User-Agent sometimes helps if blocked
        req.add_header('User-Agent', 'Mozilla/5.0')
        
        with urllib.request.urlopen(req, context=ctx) as response, open(zip_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
    except Exception as e:
        print(f"Failed to download: {e}")
        # Print response if possible to debug
        if hasattr(e, 'read'):
            print(e.read().decode())
        return

    # 3. Unzip
    print("Unzipping...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(".")
    except zipfile.BadZipFile:
        print("Error: Downloaded file is not a valid zip file.")
        return
    
    os.remove(zip_path)

    # 4. Move files from nested directory if needed
    source_dir = "finance_helper"
    if os.path.exists(source_dir):
        for item in os.listdir(source_dir):
            shutil.move(os.path.join(source_dir, item), ".")
        os.rmdir(source_dir)

    # 5. Create JSP directories
    jsp_dir = "src/main/webapp/WEB-INF/jsp"
    os.makedirs(jsp_dir, exist_ok=True)
    print(f"Created {jsp_dir}")

if __name__ == "__main__":
    setup_project()
