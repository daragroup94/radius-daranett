import os
import subprocess
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from werkzeug.utils import secure_filename
from decorators import login_required
from helpers import format_file_size

backup_bp = Blueprint('backup', __name__)

BACKUP_FOLDER = os.getenv('BACKUP_FOLDER', '/app/backups')
ALLOWED_BACKUP_EXTENSIONS = {'sql', 'backup'}

def ensure_backup_folder():
    if not os.path.exists(BACKUP_FOLDER):
        os.makedirs(BACKUP_FOLDER)

def allowed_backup_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_BACKUP_EXTENSIONS

@backup_bp.route('/backup')
@login_required
def backup_page():
    return render_template('backup/index.html')

@backup_bp.route('/api/backup/list')
@login_required
def api_list_backups():
    try:
        ensure_backup_folder() 
        backups = []
        
        if os.path.exists(BACKUP_FOLDER):
            for filename in os.listdir(BACKUP_FOLDER):
                if allowed_backup_file(filename):
                    file_path = os.path.join(BACKUP_FOLDER, filename)
                    if os.path.isfile(file_path):
                        stats = os.stat(file_path)
                        size_bytes = stats.st_size
                        created_at = datetime.fromtimestamp(stats.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
                        
                        backups.append({
                            'filename': filename,
                            'created_at': created_at,
                            'size': size_bytes,
                            'size_human': format_file_size(size_bytes)
                        })
        
        backups.sort(key=lambda x: x['created_at'], reverse=True)
        return jsonify({'success': True, 'backups': backups})
    except Exception as e:
        print(f"Error listing backups: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@backup_bp.route('/api/backup/create', methods=['POST'])
@login_required
def api_create_backup():
    try:
        ensure_backup_folder()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"radius_backup_{timestamp}.sql"
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        db_host = current_app.config['DB_HOST']
        db_name = current_app.config['DB_NAME']
        db_user = current_app.config['DB_USER']
        db_password = current_app.config['DB_PASSWORD']
        db_port = current_app.config['DB_PORT']
        
        cmd = [
            'pg_dump',
            f'--host={db_host}',
            f'--port={db_port}',
            f'--username={db_user}',
            f'--dbname={db_name}',
            '--no-password',
            '--clean',
            '--if-exists',
            '--format=plain',
            '--table=nas',
            '--table=radcheck',
            '--table=radreply',
            '--table=radusergroup',
            '--table=radgroupcheck',
            '--table=radgroupreply',
            '--table=customer_coordinates',
            '--table=nas_coordinates',
            '--table=infrastructure_coordinates',
            '--table=cable_connections',
            '--table=infrastructure_connections',

            '--table=integrations',
            '--table=admin_users',
            '--table=hotspot_vouchers',
            '--table=ont_status',
            '--table=ont_health_issues',
            '--table=nasreload',
            '--file=' + filepath
        ]
        
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        
        if result.returncode == 0:
            return jsonify({
                'success': True, 
                'message': 'Backup created successfully',
                'filename': filename
            })
        else:
            return jsonify({
                'success': False, 
                'error': f"pg_dump failed: {result.stderr}"
            }), 500
            
    except Exception as e:
        print(f"Error creating backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@backup_bp.route('/api/backup/download/<filename>')
@login_required
def api_download_backup(filename):
    try:
        if not allowed_backup_file(filename) or secure_filename(filename) != filename:
            return jsonify({'success': False, 'error': 'Invalid file name or path'}), 400
        
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        return send_file(
            filepath, 
            as_attachment=True, 
            download_name=filename
        )
    except Exception as e:
        print(f"Error downloading backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@backup_bp.route('/api/backup/delete/<filename>', methods=['DELETE'])
@login_required
def api_delete_backup(filename):
    try:
        if not allowed_backup_file(filename) or secure_filename(filename) != filename:
            return jsonify({'success': False, 'error': 'Invalid file name or path'}), 400
        
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        os.remove(filepath)
        return jsonify({
            'success': True, 
            'message': 'Backup deleted successfully'
        })
    except Exception as e:
        print(f"Error deleting backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@backup_bp.route('/api/backup/upload', methods=['POST'])
@login_required
def api_upload_backup():
    try:
        ensure_backup_folder()
        if 'backup_file' not in request.files:
            return jsonify({'success': False, 'error': 'No file part'}), 400
        
        file = request.files['backup_file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No selected file'}), 400
        
        if file and allowed_backup_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(BACKUP_FOLDER, filename)
            file.save(filepath)
            
            return jsonify({
                'success': True, 
                'message': 'Backup uploaded successfully',
                'filename': filename
            })
        else:
            return jsonify({'success': False, 'error': 'File type not allowed'}), 400
            
    except Exception as e:
        print(f"Error uploading backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@backup_bp.route('/api/backup/restore', methods=['POST'])
@login_required
def api_restore_backup():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'error': 'Invalid or missing JSON payload'}), 400
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        if not allowed_backup_file(filename) or secure_filename(filename) != filename:
            return jsonify({'success': False, 'error': 'Invalid file name or path'}), 400
        
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        db_host = current_app.config['DB_HOST']
        db_name = current_app.config['DB_NAME']
        db_user = current_app.config['DB_USER']
        db_password = current_app.config['DB_PASSWORD']
        db_port = current_app.config['DB_PORT']
        
        cmd = [
            'psql',
            f'--host={db_host}',
            f'--port={db_port}',
            f'--username={db_user}',
            f'--dbname={db_name}',
            '--no-password',
            '--file=' + filepath
        ]
        
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        
        if result.returncode == 0:
            return jsonify({
                'success': True, 
                'message': 'Database restored successfully'
            })
        else:
            return jsonify({
                'success': False, 
                'error': f"psql failed: {result.stderr}"
            }), 500
            
    except Exception as e:
        print(f"Error restoring backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
