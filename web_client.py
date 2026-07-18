import os
import subprocess
import struct
import base64
from flask import Flask, render_template, request, jsonify

from util import hot_sub_en
from util import hot_sub_zh
from util import hot_sub_rule

app = Flask(__name__)

# 获取当前文件所在目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOWED_FILES = {'hot-en.txt', 'hot-zh.txt', 'hot-rule.txt'}

def get_file_path(filename):
    if filename not in ALLOWED_FILES:
        return None
    return os.path.join(BASE_DIR, filename)

# 初始化空文件（如果不存在）
for f in ALLOWED_FILES:
    path = os.path.join(BASE_DIR, f)
    if not os.path.exists(path):
        with open(path, 'w', encoding='utf-8') as file:
            file.write("")

@app.route('/')
def index():
    return render_template('index.html')

# --- 1. 文本翻译接口 ---
@app.route('/api/translate', methods=['POST'])
def translate_text():
    data = request.json
    text = data.get('text', '')
    target_lang = data.get('lang', 'en')
    
    # 实际翻译逻辑不需要生成，pass
    pass
    
    # 模拟返回：简单加个前缀演示接口调通
    # 在实际应用中，这里会调用 Google Translate 或 LLM API
    return jsonify({'result': f"[{target_lang}] {text}"})

# --- 2. 文件修改接口 ---
@app.route('/api/file/<filename>', methods=['GET', 'POST'])
def handle_file(filename):
    file_path = get_file_path(filename)
    if not file_path:
        return jsonify({'error': 'Invalid filename'}), 400

    if request.method == 'GET':
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return jsonify({'content': content})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    elif request.method == 'POST':
        try:
            data = request.json
            content = data.get('content', '')
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

# --- 3. 文本处理接口 ---
@app.route('/api/process_text', methods=['POST'])
def process_text():
    data = request.json
    text = data.get('text', '')
    types = data.get('types', []) # ['en-hot', 'zh-hot', 'rule']
    
    # 1. 英文热词替换
    if 'en-hot' in types:
        text = hot_sub_en.热词替换(text)
        
    # 2. 中文热词替换
    if 'zh-hot' in types:
        text = hot_sub_zh.热词替换(text)
        
    # 3. 热点规则替换
    if 'rule' in types:
        text = hot_sub_rule.热词替换(text)
    
    # 返回处理后的文本（此处暂返回原文本）
    return jsonify({'result': text})


# --- 4. ffmpeg 音频提取接口 ---
@app.route('/api/extract_audio', methods=['POST'])
def extract_audio():
    """用 ffmpeg 将上传的音视频文件转为 16kHz mono Float32 PCM，返回 base64"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    uploaded = request.files['file']
    import tempfile
    tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix='_' + uploaded.filename)
    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
    tmp_in.close()
    tmp_out.close()

    try:
        uploaded.save(tmp_in.name)

        ffmpeg_path = os.path.join(BASE_DIR, 'ffmpeg.exe')
        if not os.path.exists(ffmpeg_path):
            ffmpeg_path = 'ffmpeg'

        cmd = [
            ffmpeg_path, '-y', '-i', tmp_in.name,
            '-vn',
            '-acodec', 'pcm_f32le',
            '-ar', '16000',
            '-ac', '1',
            '-f', 'wav',
            tmp_out.name
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            err_msg = result.stderr.decode('utf-8', errors='replace')[-500:]
            return jsonify({'error': f'ffmpeg failed: {err_msg}'}), 500

        with open(tmp_out.name, 'rb') as f:
            wav_data = f.read()

        data_pos = wav_data.find(b'data')
        if data_pos < 0:
            return jsonify({'error': 'Invalid WAV output'}), 500
        data_size = struct.unpack('<I', wav_data[data_pos + 4:data_pos + 8])[0]
        pcm_data = wav_data[data_pos + 8:data_pos + 8 + data_size]

        import array
        float32_arr = array.array('f')
        float32_arr.frombytes(pcm_data)
        valid_samples = [s for s in float32_arr if s == s]
        clean_bytes = array.array('f', valid_samples).tobytes()
        audio_b64 = base64.b64encode(clean_bytes).decode('ascii')

        return jsonify({'audio': audio_b64})

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Conversion timeout'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        for p in (tmp_in.name, tmp_out.name):
            try:
                os.unlink(p)
            except OSError:
                pass


def init_page():
    # 载入热词
    from util.client_hot_update import update_hot_all
    update_hot_all()
    # 启动服务，端口可根据需要修改
    print(f"Server running at http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)

if __name__ == '__main__':
    init_page()
