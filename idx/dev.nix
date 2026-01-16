# To learn more about how to use Nix to configure your environment
# see: https://firebase.google.com/docs/studio/customize-workspace
{ pkgs, ... }: {
  # Which nixpkgs channel to use.
  channel = "stable-24.05"; # or "unstable"

  # System packages to make available in the environment
  packages = [
    pkgs.python311
    pkgs.ffmpeg-full # Provides ffmpeg and ffprobe
    pkgs.yt-dlp
  ];

  # Sets environment variables in the workspace
  env = {
    PIXELDRAIN_API_KEY = "045889ef-0ac7-46b6-b685-f29d47c8803c";
  };
  idx = {
    # Recommended extension for Python development
    extensions = [ "ms-python.python" ];

    # Configure the web preview for your Flask application
    previews = {
      enable = true;
      previews = {
        web = {
          command = [".venv/bin/python" "app.py"];
          manager = "web";
          env = {
            PORT = "$PORT";
            
            # --- THIS IS THE CORRECTED AND SIMPLIFIED FIX ---
            # We explicitly define the PATH for the preview server.
            # 1. "$PWD/.venv/bin" ensures your pip-installed tools like yt-dlp are found.
            # 2. "${pkgs.ffmpeg-full}/bin" adds the directory for ffmpeg.
            # 3. "$PATH" includes the standard system paths.
            PATH = "$PWD/.venv/bin:${pkgs.ffmpeg-full}/bin:$PATH";
          };
        };
      };
    };

    # Workspace lifecycle hooks
    workspace = {
      # Runs ONCE when a workspace is first created
      onCreate = {
        setup-python-env = ''
          # Create a virtual environment if it doesn't exist
          if [ ! -d ".venv" ]; then
            python3 -m venv .venv
            echo "Virtual environment created."
          fi
          # Install/update packages from requirements.txt
          echo "Installing Python packages..."
          source .venv/bin/activate && pip install -r requirements.txt
        '';
      };

      # Runs every time the workspace is (re)started
      onStart = {};
    };
  };
}
