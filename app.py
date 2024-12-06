from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
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
            'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            'outtmpl': output_path,
            'merge_output_format': 'mp4',
            'quiet': False,  # Activar logs para diagnóstico
            'no_warnings': False,  # Mostrar advertencias
            'verbose': True,  # Mostrar información detallada
            'progress': True,  # Mostrar progreso
            'extract_flat': False,
            'force_generic_extractor': False,
            'ignoreerrors': False,  # No ignorar errores para poder diagnosticarlos
            'nocheckcertificate': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'logger': app.logger,  # Usar el logger de Flask
        }

        # Intentar la descarga
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                print("Extrayendo información del video...")
                info = ydl.extract_info(url, download=False)
                if not info:
                    return jsonify({'success': False, 'error': 'No se pudo obtener información del video'})

                print("Información extraída, iniciando descarga...")
                ydl.download([url])

                # Encontrar el archivo descargado
                expected_file = os.path.join(app.config['UPLOAD_FOLDER'], f'{temp_filename}.mp4')
                print(f"Buscando archivo descargado en: {expected_file}")

                if not os.path.exists(expected_file):
                    # Buscar cualquier archivo que coincida con el patrón
                    possible_files = glob.glob(os.path.join(app.config['UPLOAD_FOLDER'], f'{temp_filename}.*'))
                    if possible_files:
                        downloaded_file = possible_files[0]
                        print(f"Archivo encontrado con extensión diferente: {downloaded_file}")
                        
                        # Convertir a MP4 si es necesario
                        if not downloaded_file.endswith('.mp4'):
                            new_filename = f"{downloaded_file[:-4]}.mp4"
                            os.rename(downloaded_file, new_filename)
                            downloaded_file = new_filename
                    else:
                        print("No se encontró ningún archivo descargado")
                        return jsonify({
                            'success': False,
                            'error': 'No se pudo encontrar el archivo descargado'
                        })
                else:
                    downloaded_file = expected_file

                print(f"Descarga completada: {downloaded_file}")

                # Verificar el archivo
                if os.path.getsize(downloaded_file) == 0:
                    os.remove(downloaded_file)
                    return jsonify({
                        'success': False,
                        'error': 'El archivo descargado está vacío'
                    })

                return jsonify({
                    'success': True,
                    'filename': os.path.basename(downloaded_file),
                    'metadata': {
                        'title': info.get('title', ''),
                        'duration': info.get('duration', 0),
                        'view_count': info.get('view_count', 0),
                        'uploader': info.get('uploader', ''),
                    }
                })

        except Exception as e:
            print(f"Error durante la descarga: {str(e)}")
            # Intentar con configuración alternativa
            alt_opts = {
                **ydl_opts,
                'format': 'best',  # Formato más simple
                'postprocessors': [],  # Sin post-procesamiento
            }
            
            print("Intentando con configuración alternativa...")
            with yt_dlp.YoutubeDL(alt_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    final_file = os.path.join(app.config['UPLOAD_FOLDER'], f'{temp_filename}.mp4')
                    if os.path.exists(final_file):
                        return jsonify({
                            'success': True,
                            'filename': os.path.basename(final_file),
                            'metadata': {
                                'title': info.get('title', ''),
                                'duration': info.get('duration', 0),
                                'view_count': info.get('view_count', 0),
                                'uploader': info.get('uploader', ''),
                            }
                        })
            
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
    return download_video()

@app.route('/process', methods=['POST'])
def process_video():
    try:
        if 'video' not in request.form:
            return jsonify({'success': False, 'error': 'No se especificó el video'})
        
        video_filename = request.form['video']
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)
        metadata_path = os.path.join(app.config['UPLOAD_FOLDER'], 'metadata.json')
        
        if not os.path.exists(video_path):
            app.logger.error(f"Video no encontrado en: {video_path}")
            return jsonify({'success': False, 'error': 'Video no encontrado'})

        # Cargar metadatos originales
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                original_metadata = json.load(f)
        except Exception as e:
            app.logger.error(f"Error cargando metadatos: {str(e)}")
            original_metadata = {}

        # Obtener overlay
        if 'overlay' not in request.files:
            return jsonify({'success': False, 'error': 'No se subió ningún overlay'})
        
        overlay_file = request.files['overlay']
        if not overlay_file.filename:
            return jsonify({'success': False, 'error': 'Archivo de overlay inválido'})
            
        overlay_filename = secure_filename(overlay_file.filename)
        overlay_path = os.path.join(app.config['UPLOAD_FOLDER'], f'overlay_{int(time.time())}_{overlay_filename}')
        overlay_file.save(overlay_path)

        app.logger.info(f"Procesando video: {video_path}")
        app.logger.info(f"Con overlay: {overlay_path}")

        # Obtener posición y configuración
        try:
            position = json.loads(request.form.get('position', '{}'))
            scale = float(request.form.get('scale', 100)) / 100
            opacity = float(request.form.get('opacity', 100)) / 100
            
            app.logger.info(f"Posición: {position}")
            app.logger.info(f"Escala: {scale}")
            app.logger.info(f"Opacidad: {opacity}")
        except (ValueError, json.JSONDecodeError) as e:
            return jsonify({'success': False, 'error': f'Error en los parámetros: {str(e)}'})

        # Crear nombre para el video procesado
        output_filename = f'processed_{int(time.time())}.mp4'
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)

        # Procesar el video
        video = None
        overlay_img = None
        final_video = None
        temp_overlay_path = None
        
        try:
            # Cargar video
            video = VideoFileClip(video_path)
            if not video.size:
                raise ValueError("No se pudo obtener el tamaño del video")

            # Cargar overlay directamente como ImageClip
            overlay_img = ImageClip(overlay_path, transparent=True)
            
            # Obtener dimensiones originales
            original_width, original_height = overlay_img.size
            
            # Calcular nuevo tamaño
            new_width = int(original_width * scale)
            new_height = int(original_height * scale)
            
            # Redimensionar el overlay
            overlay_img = overlay_img.resize((new_width, new_height))
            
            # Obtener posición
            x = position.get('x', 0)
            y = position.get('y', 0)
            
            # Asegurar que el overlay no se salga del video
            x = max(0, min(x, video.size[0] - new_width))
            y = max(0, min(y, video.size[1] - new_height))
            
            app.logger.info(f"Video size: {video.size}")
            app.logger.info(f"Overlay original size: {(original_width, original_height)}")
            app.logger.info(f"Overlay new size: {(new_width, new_height)}")
            app.logger.info(f"Position: ({x}, {y})")
            app.logger.info(f"Scale: {scale}")
            app.logger.info(f"Opacity: {opacity}")
            
            # Configurar overlay con posición y opacidad
            overlay = (overlay_img
                      .set_position((x, y))
                      .set_opacity(opacity)
                      .set_duration(video.duration))

            # Componer video final manteniendo el tamaño original
            final_video = CompositeVideoClip([video, overlay])
            
            app.logger.info("Guardando video procesado...")
            
            # Guardar con los mismos parámetros del video original
            final_video.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                fps=video.fps,
                preset='medium',
                threads=4
            )
            
            app.logger.info("Video guardado exitosamente")

            # Preparar metadatos
            metadata = {
                'title': original_metadata.get('title', 'Video procesado'),
                'description': original_metadata.get('description', ''),
                'tags': original_metadata.get('tags', []),
                'duration': video.duration,
                'uploader': original_metadata.get('uploader', ''),
                'filename': output_filename,
                'processed': True,
                'overlay': {
                    'position': {'x': x, 'y': y},
                    'scale': scale,
                    'opacity': opacity,
                    'dimensions': {
                        'width': new_width,
                        'height': new_height
                    }
                }
            }

            # Guardar metadatos del video procesado
            processed_metadata_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{output_filename}_metadata.json')
            with open(processed_metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            app.logger.info("Proceso completado exitosamente")
            return jsonify({
                'success': True,
                'video_id': output_filename,
                'metadata': metadata
            })

        except Exception as e:
            app.logger.error(f"Error procesando video: {str(e)}")
            return jsonify({'success': False, 'error': f'Error al procesar el video: {str(e)}'})

        finally:
            # Limpiar recursos
            try:
                if video:
                    video.close()
                if overlay_img:
                    overlay_img.close()
                if final_video:
                    final_video.close()
                if os.path.exists(overlay_path):
                    os.remove(overlay_path)
                if temp_overlay_path and os.path.exists(temp_overlay_path):
                    os.remove(temp_overlay_path)
            except Exception as e:
                app.logger.error(f"Error limpiando recursos: {str(e)}")

    except Exception as e:
        app.logger.error(f"Error inesperado: {str(e)}")
        return jsonify({'success': False, 'error': f'Error inesperado: {str(e)}'})

@app.route('/result/<video_id>')
def result(video_id):
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_id)
    metadata_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{video_id}_metadata.json')
    
    if not os.path.exists(video_path):
        return "Video no encontrado", 404

    try:
        # Intentar cargar metadatos guardados
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except Exception as e:
            app.logger.warning(f"Error cargando metadatos guardados: {str(e)}")
            # Si no hay metadatos guardados, obtener información básica del video
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
        
        return render_template('result.html', video_id=video_id, metadata=metadata)
    except Exception as e:
        app.logger.error(f"Error obteniendo metadatos del video: {str(e)}")
        return "Error al cargar el video", 500

@app.route('/video/<path:filename>')
def serve_video(filename):
    """Servir archivos de video desde el directorio de uploads"""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except Exception as e:
        app.logger.error(f"Error serving video: {str(e)}")
        return "Error al cargar el video", 404

if __name__ == '__main__':
    app.run(debug=True)
