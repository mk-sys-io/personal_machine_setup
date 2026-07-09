# fzf: use fd as file source (hides dotfiles by default)
export FZF_DEFAULT_COMMAND='fdfind --type f --strip-cwd-prefix'
export FZF_CTRL_T_COMMAND='fdfind --type f --type d --strip-cwd-prefix'
export FZF_ALT_C_COMMAND='fdfind --type d --strip-cwd-prefix'

_fzf_compgen_path() { fdfind --exclude ".git" . "$1"; }
_fzf_compgen_dir()  { fdfind --type d --exclude ".git" . "$1"; }
