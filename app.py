from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, url_for
import yt_dlp
import os
import cv2
import json
from werkzeug.utils import secure_filename
import shutil
import time
import subprocess
from moviepy.editor import VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip
import re
import unicodedata
import glob
from PIL import Image

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PROCESSED_FOLDER'] = 'processed'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# Crear directorios necesarios al inicio de la aplicación
#os.makedirs('downloads', exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROCESSED_FOLDER'], exist_ok=True)

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

def ensure_upload_folder():
    """Asegura que la carpeta de uploads existe y tiene los permisos correctos"""
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Verificar permisos de escritura
    test_file = os.path.join(app.config['UPLOAD_FOLDER'], 'test.txt')
    try:
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
    except Exception as e:
        print(f"Error al verificar permisos de escritura: {e}")
        raise

def download_video():
    try:
        # Asegurar que la carpeta de uploads existe
        ensure_upload_folder()
        
        url = request.form.get('url')
        if not url:
            return jsonify({'success': False, 'error': 'URL no proporcionada'})

        print(f"Iniciando descarga de URL: {url}")

        # Crear nombre de archivo temporal único
        timestamp = int(time.time())
        temp_filename = f'video_{timestamp}'
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{temp_filename}.%(ext)s')
        
        print(f"Ruta de salida: {output_path}")

        # Configurar opciones de yt-dlp
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',  # Mejor video + mejor audio, o mejor calidad combinada
            'outtmpl': output_path,
            'quiet': False,
            'no_warnings': False,
            'verbose': True,
            'progress': True,
            'nocheckcertificate': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'writeinfojson': True,  # Guardar metadata en JSON
            'writethumbnail': True,  # Guardar thumbnail
        }

        # Intentar la descarga
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                print("Descargando video...")
                info = ydl.extract_info(url, download=True)
                if not info:
                    return jsonify({'success': False, 'error': 'No se pudo obtener información del video'})

                # Encontrar el archivo descargado
                video_filename = f'{temp_filename}.mp4'
                downloaded_file = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)

                # Guardar metadata en un archivo JSON
                metadata = {
                    'title': info.get('title', ''),
                    'description': info.get('description', ''),
                    'duration': info.get('duration', 0),
                    'view_count': info.get('view_count', 0),
                    'uploader': info.get('uploader', ''),
                    'tags': info.get('tags', []),
                    'thumbnail': info.get('thumbnail', ''),
                }
                
                metadata_path = os.path.join(app.config['UPLOAD_FOLDER'], 'metadata.json')
                with open(metadata_path, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=4)

                print(f"Archivo descargado: {downloaded_file}")
                print(f"Metadata guardada en: {metadata_path}")

                # Verificar el archivo
                if os.path.getsize(downloaded_file) == 0:
                    os.remove(downloaded_file)
                    return jsonify({
                        'success': False,
                        'error': 'El archivo descargado está vacío'
                    })

                return jsonify({
                    'success': True,
                    'filename': video_filename,
                    'metadata': metadata
                })

        except Exception as e:
            print(f"Error durante la descarga: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Error en la descarga: {str(e)}'
            })

    except Exception as e:
        print(f"Error inesperado: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error inesperado: {str(e)}'
        })

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    result = download_video()
    if isinstance(result, str):
        return result
    
    response_data = json.loads(result.get_data(as_text=True))
    if response_data['success']:
        # Agregar la URL del video descargado a la respuesta
        video_url = url_for('serve_video', 
                          filename=response_data['filename'],
                          folder='upload')  # Importante: especificar folder='upload'
        response_data['video_url'] = video_url
        
        app.logger.info(f"Video descargado: {response_data['filename']}")
        app.logger.info(f"URL del video: {video_url}")
    
    return jsonify(response_data)

@app.route('/process', methods=['POST'])
def process_video():
    try:
        if 'video' not in request.form:
            return jsonify({'success': False, 'error': 'No se especificó el video'})
        
        video_filename = request.form['video']
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)
        
        # Obtener overlay y configuración
        if 'overlay' not in request.files:
            return jsonify({'success': False, 'error': 'No se subió ningún overlay'})
        
        overlay_file = request.files['overlay']
        
        # Obtener dimensiones del contenedor y del video real
        container_dims = json.loads(request.form.get('containerDimensions', '{}'))
        container_width = float(container_dims.get('width', 1))
        container_height = float(container_dims.get('height', 1))
        
        # Obtener posición relativa al contenedor
        position = json.loads(request.form.get('position', '{}'))
        relative_x = float(position.get('x', 0)) / container_width
        relative_y = float(position.get('y', 0)) / container_height
        
        # Obtener escala y opacidad
        scale = float(request.form.get('scale', '100')) / 100
        opacity = float(request.form.get('opacity', '100')) / 100
        
        # Cargar video para obtener dimensiones reales
        video = VideoFileClip(video_path)
        video_width, video_height = video.size
        
        # Calcular posición real en el video
        x = relative_x * video_width
        y = relative_y * video_height
        
        # Procesar overlay
        overlay_path = os.path.join(app.config['UPLOAD_FOLDER'], f'overlay_{int(time.time())}_{secure_filename(overlay_file.filename)}')
        overlay_file.save(overlay_path)
        
        # Usar PIL para el redimensionamiento preciso
        from PIL import Image
        
        # Abrir y procesar overlay
        with Image.open(overlay_path) as pil_image:
            # Obtener dimensiones originales del overlay
            original_width, original_height = pil_image.size
            
            # Calcular nuevas dimensiones manteniendo la proporción con el video
            overlay_max_width = video_width * 0.3  # máximo 30% del ancho del video
            base_scale = overlay_max_width / original_width
            final_scale = base_scale * scale
            
            new_width = int(original_width * final_scale)
            new_height = int(original_height * final_scale)
            
            # Redimensionar overlay
            pil_image = pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Guardar overlay procesado
            processed_overlay_path = os.path.join(app.config['UPLOAD_FOLDER'], f'processed_overlay_{int(time.time())}.png')
            pil_image.save(processed_overlay_path, 'PNG')
        
        # Crear clip de overlay
        overlay_clip = (ImageClip(processed_overlay_path, transparent=True)
                       .set_position((x, y))
                       .set_opacity(opacity)
                       .set_duration(video.duration))
        
        # Logs para debugging
        app.logger.info(f"Dimensiones del video: {video_width}x{video_height}")
        app.logger.info(f"Dimensiones originales del overlay: {original_width}x{original_height}")
        app.logger.info(f"Nuevas dimensiones del overlay: {new_width}x{new_height}")
        app.logger.info(f"Posición final del overlay: ({x}, {y})")
        app.logger.info(f"Escala aplicada: {scale}")
        
        # Crear video final
        final_video = CompositeVideoClip([video, overlay_clip])
        
        # Guardar video procesado
        output_filename = f'processed_{int(time.time())}.mp4'
        output_path = os.path.join(app.config['PROCESSED_FOLDER'], output_filename)
        
        final_video.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac',
            fps=video.fps,
            preset='slow',
            bitrate='8000k'
        )
        
        # Limpiar recursos
        video.close()
        overlay_clip.close()
        final_video.close()
        os.remove(overlay_path)
        os.remove(processed_overlay_path)
        
        return jsonify({
            'success': True,
            'video_id': output_filename
        })
        
    except Exception as e:
        app.logger.error(f"Error procesando video: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/result/<video_id>')
def result(video_id):
    video_path = os.path.join(app.config['PROCESSED_FOLDER'], video_id)
    metadata_path = os.path.join(app.config['PROCESSED_FOLDER'], f'{video_id}_metadata.json')
    
    if not os.path.exists(video_path):
        return "Video no encontrado", 404

    try:
        # Cargar metadatos
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except Exception as e:
            app.logger.warning(f"Error cargando metadatos guardados: {str(e)}")
            video = VideoFileClip(video_path)
            metadata = {
                'title': 'Video procesado',
                'description': '',
                'tags': [],
                'duration': video.duration,
                'uploader': '',
                'filename': video_id
            }
            video.close()
        
        # Agregar el video_id y output_filename a los datos enviados al template
        return render_template('result.html', 
                            video_id=video_id, 
                            output_filename=video_id,  # Agregar esta línea
                            metadata=metadata)
    except Exception as e:
        app.logger.error(f"Error obteniendo metadatos del video: {str(e)}")
        return "Error al cargar el video", 500

@app.route('/video/<path:filename>')
def serve_video(filename):
    """Servir archivos de video desde el directorio especificado"""
    try:
        folder = request.args.get('folder', 'processed')  # Por defecto sirve desde processed
        
        if folder == 'upload':
            directory = app.config['UPLOAD_FOLDER']
        else:
            directory = app.config['PROCESSED_FOLDER']
            
        app.logger.info(f"Sirviendo video desde {directory}: {filename}")
        
        if not os.path.exists(os.path.join(directory, filename)):
            app.logger.error(f"Archivo no encontrado: {filename} en {directory}")
            return "Archivo no encontrado", 404
            
        return send_from_directory(directory, filename)
    except Exception as e:
        app.logger.error(f"Error serving video: {str(e)}")
        return f"Error al cargar el video: {str(e)}", 404

if __name__ == '__main__':
    #app.run(debug=True)
    port = int(os.environ.get("PORT", 5000))  # Toma el puerto del entorno, con un predeterminado en caso de estar en local.
    app.run(host="0.0.0.0", port=port, debug=True)
