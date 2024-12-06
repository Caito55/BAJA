from flask import Flask, render_template, request, jsonify, send_file
import yt_dlp
import os
import cv2
import json
from werkzeug.utils import secure_filename
import shutil
import time
import subprocess
from moviepy.editor import VideoFileClip, AudioFileClip
import re
import unicodedata

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# Crear directorios necesarios al inicio de la aplicación
os.makedirs('downloads', exist_ok=True)
os.makedirs('processed', exist_ok=True)
os.makedirs('uploads', exist_ok=True)

def sanitize_filename(filename):
    """
    Sanitize filename to remove or replace problematic characters
    
    Args:
        filename (str): Original filename
    
    Returns:
        str: Sanitized filename safe for filesystem
    """
    # Normalize unicode characters
    filename = unicodedata.normalize('NFKD', filename)
    
    # Remove non-ASCII characters
    filename = filename.encode('ascii', 'ignore').decode('ascii')
    
    # Replace spaces and special characters
    filename = re.sub(r'[^\w\-_\.]', '_', filename)
    
    # Remove leading/trailing spaces and dots
    filename = filename.strip('. ')
    
    # Limit filename length
    max_length = 255
    if len(filename) > max_length:
        filename = filename[:max_length]
    
    # Ensure filename is not empty
    if not filename:
        filename = 'unnamed_video'
    
    return filename

def download_video(url):
    ydl_opts = {
        'format': 'best[ext=mp4]',  # Preferir formato MP4
        'outtmpl': os.path.join('downloads', '%(title)s.%(ext)s'),
        'writeinfojson': True,
        'quiet': False,
        'no_warnings': False,
        'extract_flat': False,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
        # Manejar shorts y videos normales
        'extractor_args': {
            'youtube': {
                'skip': ['dash', 'hls'],
            },
        },
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # Sanitizar el título del video para usar como nombre de archivo
            video_title = sanitize_filename(info.get('title', 'video'))
            
            # Renombrar el archivo descargado con el título sanitizado
            ext = filename.split('.')[-1]
            sanitized_filename = f'{video_title}.{ext}'
            os.rename(filename, os.path.join('downloads', sanitized_filename))
            
            # Guardar metadatos
            metadata = {
                'title': info.get('title', ''),
                'description': info.get('description', ''),
                'tags': info.get('tags', [])
            }
            
            with open('downloads/metadata.json', 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=4)
                
            return sanitized_filename, metadata
    except Exception as e:
        print(f"Error durante la descarga: {str(e)}")
        raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    if not url:
        return jsonify({'error': 'URL no proporcionada'}), 400
    
    try:
        # Imprimir la URL recibida para depuración
        print(f"Descargando URL: {url}")
        
        # Usar request.form.get('url') en lugar de request.form['url']
        filename, metadata = download_video(url)
        return jsonify({
            'success': True,
            'filename': os.path.basename(filename),
            'metadata': metadata
        })
    except Exception as e:
        # Imprimir el error completo para depuración
        import traceback
        print("Error completo durante la descarga:")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/process', methods=['POST'])
def process_video():
    # Verificar que se haya proporcionado un overlay
    if 'overlay' not in request.files:
        return jsonify({'error': 'No se proporcionó overlay'}), 400
    
    # Obtener el nombre del video
    video_file = request.form.get('video')
    if not video_file:
        return jsonify({'error': 'No se especificó el video'}), 400
    
    try:
        # Imprimir información para depuración
        print(f"Procesando video: {video_file}")
        
        # Obtener parámetros adicionales
        position = json.loads(request.form.get('position', '{"x": 0, "y": 0}'))
        scale = float(request.form.get('scale', '100')) / 100
        opacity = float(request.form.get('opacity', '100')) / 100
        
        # Guardar el archivo de overlay
        overlay_file = request.files['overlay']
        overlay_filename = secure_filename(overlay_file.filename)
        overlay_path = os.path.join(app.config['UPLOAD_FOLDER'], overlay_filename)
        overlay_file.save(overlay_path)
        
        # Generar un ID único para el video procesado
        video_id = f"processed_{int(time.time())}"
        temp_output = os.path.join('processed', f'{video_id}_temp.mp4')
        final_output = os.path.join('processed', f'{video_id}.mp4')
        
        # Procesar video con overlay
        input_video = os.path.join('downloads', video_file)
        
        cap = cv2.VideoCapture(input_video)
        overlay_img = cv2.imread(overlay_path, cv2.IMREAD_UNCHANGED)
        
        # Obtener propiedades del video
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        
        # Calcular dimensiones del overlay con escala
        overlay_width = int(overlay_img.shape[1] * scale)
        overlay_height = int(overlay_img.shape[0] * scale)
        overlay_img = cv2.resize(overlay_img, (overlay_width, overlay_height))
        
        # Calcular posición del overlay
        x = int(position['x'])
        y = int(position['y'])
        
        # Asegurarse de que el overlay no se salga del frame
        x = max(0, min(x, frame_width - overlay_width))
        y = max(0, min(y, frame_height - overlay_height))
        
        # Configurar el writer con codec H.264
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(temp_output, fourcc, fps, (frame_width, frame_height))
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        processed_frames = 0
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            # Crear una región de interés (ROI) para el overlay
            try:
                roi = frame[y:y + overlay_height, x:x + overlay_width]
                
                # Si el overlay tiene canal alpha
                if overlay_img.shape[2] == 4:
                    # Separar el canal alpha y aplicar la opacidad
                    alpha = overlay_img[:, :, 3] / 255.0 * opacity
                    alpha = cv2.merge([alpha, alpha, alpha])
                    
                    # Separar los canales BGR
                    overlay_bgr = overlay_img[:, :, :3]
                    
                    # Combinar overlay con el frame
                    roi_result = cv2.multiply(1.0 - alpha, roi.astype(float)) + \
                               cv2.multiply(alpha, overlay_bgr.astype(float))
                    
                    # Reemplazar la región en el frame original
                    frame[y:y + overlay_height, x:x + overlay_width] = roi_result.astype('uint8')
            except ValueError as e:
                print(f"Error procesando frame: {str(e)}")
                continue
            
            out.write(frame)
            
            processed_frames += 1
            progress = (processed_frames / total_frames) * 100
            print(f"Progreso: {progress:.2f}%")
        
        cap.release()
        out.release()

        # Intentar usar moviepy para combinar audio y video
        try:
            # Cargar el video procesado y el video original
            processed_video = VideoFileClip(temp_output)
            original_video = VideoFileClip(input_video)
            
            # Obtener el audio del video original
            audio = original_video.audio
            
            if audio is not None:
                # Combinar el video procesado con el audio original
                final_video = processed_video.set_audio(audio)
                
                # Escribir el video final
                final_video.write_videofile(final_output, 
                                         codec='libx264', 
                                         audio_codec='aac',
                                         temp_audiofile='temp-audio.m4a',
                                         remove_temp=True)
            else:
                # Si no hay audio, solo renombrar el archivo temporal
                os.rename(temp_output, final_output)
            
            # Cerrar los clips
            processed_video.close()
            original_video.close()
            
        except Exception as e:
            print(f"Error procesando audio: {str(e)}")
            # Si hay error, usar el video sin audio
            os.rename(temp_output, final_output)
        
        # Limpiar archivos temporales
        if os.path.exists(temp_output):
            os.remove(temp_output)
        
        # Copiar los metadatos originales
        shutil.copy('downloads/metadata.json', f'processed/{video_id}_metadata.json')
        
        return jsonify({
            'success': True,
            'video_id': video_id,
            'redirect_url': f'/result/{video_id}'
        })
        
    except Exception as e:
        # Imprimir el error completo para depuración
        import traceback
        print("Error durante el procesamiento del video:")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/result/<video_id>')
def result_page(video_id):
    try:
        # Leer los metadatos guardados
        with open(f'processed/{video_id}_metadata.json', 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        return render_template('result.html', 
                             video_id=video_id,
                             title=metadata.get('title', ''),
                             description=metadata.get('description', ''),
                             tags=metadata.get('tags', []))

    except Exception as e:
        print(f"Error al cargar metadatos: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download_processed/<video_id>')
def download_processed(video_id):
    video_path = os.path.join('processed', f'{video_id}.mp4')
    if os.path.exists(video_path):
        return send_file(video_path, as_attachment=True)
    return jsonify({'error': 'Video no encontrado'}), 404

@app.route('/video/<filename>')
def serve_video(filename):
    # Sanitizar el nombre del archivo antes de usarlo
    safe_filename = sanitize_filename(filename)
    
    try:
        return send_file(os.path.join('downloads', safe_filename))
    except FileNotFoundError:
        # Intentar encontrar el archivo con diferentes extensiones
        for ext in ['mp4', 'mkv', 'avi', 'webm']:
            possible_filename = f'{safe_filename}.{ext}'
            possible_path = os.path.join('downloads', possible_filename)
            if os.path.exists(possible_path):
                return send_file(possible_path)
        
        # Si no se encuentra el archivo, devolver un error
        return "Video no encontrado", 404

@app.route('/processed/<filename>')
def serve_processed_video(filename):
    return send_file(os.path.join('processed', filename))

if __name__ == '__main__':
    app.run(debug=True)
