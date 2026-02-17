"""
Session Manager - Handles session save/load/delete operations
"""

import os
import json
from datetime import datetime
from pathlib import Path
import re


class SessionManager:
    """Manages chat sessions - save, load, list, delete, rename"""
    
    def __init__(self):
        self.sessions_dir = Path("data/sessions")
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.active_session_id = None
        self.session_metadata = {}  # Cache for session names/dates
        
    def create_session(self):
        """Create a new session with timestamp-based ID"""
        now = datetime.now()
        # Format: 2_15_2026_15_07_22_45
        session_id = now.strftime("%m_%d_%Y_%H_%M_%S_%f")[:-4]  # Remove last 2 digits of microseconds
        
        creation_time = now.strftime("%B %d, %Y - %I:%M%p").replace(" 0", " ")  # Remove leading zero from hour
        
        session_data = {
            "session_name": "New Session",
            "creation_time_and_date": creation_time,
            "id": session_id,
            "chat_history": []
        }
        
        # Don't save empty session yet - will be saved when first message is sent
        self.session_metadata[session_id] = {
            "name": "New Session",
            "date": creation_time,
            "id": session_id
        }
        
        return session_id
        
    def save_session(self, session_id, chat_history, session_name=None):
        """Save session to JSON file"""
        if not session_id:
            return False
            
        # Get metadata
        metadata = self.session_metadata.get(session_id, {})
        
        # Use provided name or cached name
        if session_name:
            metadata['name'] = session_name
        
        session_data = {
            "session_name": metadata.get('name', 'New Session'),
            "creation_time_and_date": metadata.get('date', ''),
            "id": session_id,
            "chat_history": chat_history
        }
        
        # Get current filename
        session_file = self._get_session_file(session_id)
        
        try:
            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving session: {e}")
            return False
            
    def load_session(self, session_id):
        """Load session from JSON file"""
        session_file = self._get_session_file(session_id)
        
        if not session_file.exists():
            return None
            
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
                
            # Update metadata cache
            self.session_metadata[session_id] = {
                "name": session_data.get('session_name', 'New Session'),
                "date": session_data.get('creation_time_and_date', ''),
                "id": session_id
            }
            
            return session_data
        except Exception as e:
            print(f"Error loading session: {e}")
            return None
            
    def list_sessions(self):
        """List all sessions, sorted by date (newest first)"""
        sessions = []
        
        for file_path in self.sessions_dir.glob("*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)
                    
                sessions.append({
                    'id': session_data.get('id', file_path.stem),
                    'name': session_data.get('session_name', 'Unnamed'),
                    'date': session_data.get('creation_time_and_date', ''),
                    'file': file_path.name
                })
            except:
                pass
                
        # Sort by ID (which is timestamp-based) - newest first
        sessions.sort(key=lambda x: x['id'], reverse=True)
        return sessions
        
    def delete_session(self, session_id):
        """Delete a session file"""
        session_file = self._get_session_file(session_id)
        
        if session_file.exists():
            try:
                session_file.unlink()
                if session_id in self.session_metadata:
                    del self.session_metadata[session_id]
                return True
            except Exception as e:
                print(f"Error deleting session: {e}")
                return False
        return False
        
    def rename_session(self, session_id, new_name):
        """Rename a session (updates filename and JSON)"""
        old_file = self._get_session_file(session_id)
        
        if not old_file.exists():
            return False
            
        # Sanitize name for filename
        clean_name = self._sanitize_filename(new_name)
        
        # Create new filename: Name_2_15_2026_15_07_22_45.json
        new_filename = f"{clean_name}_{session_id}.json"
        new_file = self.sessions_dir / new_filename
        
        try:
            # Load current data
            with open(old_file, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
                
            # Update name in data
            session_data['session_name'] = new_name
            
            # Save to new filename
            with open(new_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
                
            # Delete old file if different
            if old_file != new_file:
                old_file.unlink()
                
            # Update metadata cache
            self.session_metadata[session_id] = {
                "name": new_name,
                "date": session_data.get('creation_time_and_date', ''),
                "id": session_id
            }
            
            return True
        except Exception as e:
            print(f"Error renaming session: {e}")
            return False
            
    def _get_session_file(self, session_id):
        """Get the file path for a session ID"""
        # Check if file exists with prefix (renamed)
        for file_path in self.sessions_dir.glob(f"*{session_id}.json"):
            return file_path
            
        # Default filename (not renamed yet)
        return self.sessions_dir / f"{session_id}.json"
        
    def _sanitize_filename(self, name):
        """Sanitize session name for use in filename"""
        # Remove or replace invalid characters
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        # Replace spaces with underscores
        name = name.replace(' ', '_')
        # Limit length
        name = name[:50]
        # Remove leading/trailing underscores
        name = name.strip('_')
        return name if name else "Session"
        
    def get_session_name(self, session_id):
        """Get the name of a session"""
        if session_id in self.session_metadata:
            return self.session_metadata[session_id].get('name', 'New Session')
            
        # Load from file
        session_data = self.load_session(session_id)
        if session_data:
            return session_data.get('session_name', 'New Session')
            
        return 'New Session'
        
    def get_session_date(self, session_id):
        """Get the creation date of a session"""
        if session_id in self.session_metadata:
            return self.session_metadata[session_id].get('date', '')
            
        # Load from file
        session_data = self.load_session(session_id)
        if session_data:
            return session_data.get('creation_time_and_date', '')
            
        return ''
