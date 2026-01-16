# Video Downloader & Uploader

## Overview
A full-featured Flask web application for downloading, encoding, and uploading videos. Built for Replit deployment with password protection and comprehensive video processing capabilities.

## Current State
- **Status**: Fully functional on Replit
- **Last Updated**: October 16, 2025
- **Deployment**: Replit (private access via password)

## Features
- YouTube and multi-source video downloading (yt-dlp)
- Advanced video encoding (H.265/x265, AV1/SVT-AV1)
- Real-time progress tracking via Server-Sent Events (SSE)
- Pixeldrain integration for file uploads
- Video format selection with audio/video stream mixing
- File management (delete, rename, encode)
- YouTube cookies support for restricted content
- Password protection (HTTP Basic Auth)

## User Preferences
- **Password Protection**: Basic auth with username "admin" and APP_PASSWORD from environment
- **Video Quality**: Supports 4K/1080p/720p downloads with custom encoding presets
- **Encoding Defaults**: H.265 CRF 28 (faster preset) or AV1 CRF 35 (preset 6)
- **Personal Use**: Private access, not for public sharing

## Architecture

### Tech Stack
- **Backend**: Flask 3.1+ (Python 3.11)
- **Video Processing**: yt-dlp, ffmpeg-full
- **Cloud Storage**: Pixeldrain API
- **Progress Tracking**: Server-Sent Events (SSE)

### Key Components
- `app.py`: Main Flask application
- `/downloads`: Persistent storage for downloaded files
- `youtube_cookies.txt`: YouTube authentication cookies (optional)

### Environment Variables
- `SESSION_SECRET`: Flask session secret key (auto-generated if not set)
- `APP_PASSWORD`: Authentication password (default: "1234")
- `PIXELDRAIN_API_KEY`: Optional Pixeldrain API key for uploads

## Recent Changes
- **2025-10-16**: Migrated from Railway to Replit
  - Removed Railway-specific ffmpeg installation code
  - Updated to use Replit environment variables (SESSION_SECRET)
  - Configured ffmpeg via replit.nix (system dependency)
  - Updated Python package management for Replit's uv system

## Development Notes
- Dependencies managed via Replit's packager_tool (uv)
- ffmpeg-full includes libx265 and libsvtav1 for encoding
- All secrets stored in Replit environment (never in code)
- Downloads folder automatically created on startup
