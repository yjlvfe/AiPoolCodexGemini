"""
Web Dashboard Server Pro (Luxury OLED Glass Theme, Custom Animated Dropdowns, Micro-Interactions)
"""
import http.server
import socketserver
import urllib.parse
import json
import time
import threading
import os
from http import cookies
import subprocess
from app import PoolManager, TokenAuthManager

PORT = int(os.environ.get('DASHBOARD_PORT', '8444'))
auth_manager = TokenAuthManager()
HTML_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AI Pools Gateway • Access Restricted</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Cairo:wght@600;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #06080d;
            --card-bg: rgba(13, 17, 26, 0.75);
            --card-border: rgba(255, 255, 255, 0.08);
            --accent-cyan: #00f2fe;
            --accent-purple: #9d4edd;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        body {
            background-color: var(--bg-base);
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(0, 242, 254, 0.08) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(157, 78, 221, 0.08) 0%, transparent 40%);
            color: var(--text-main);
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-card {
            background: var(--card-bg);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid var(--card-border);
            border-radius: 28px;
            padding: 36px 28px;
            width: 100%;
            max-width: 420px;
            text-align: center;
            box-shadow: 0 24px 64px -12px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            animation: floatUp 0.6s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes floatUp {
            from { opacity: 0; transform: translateY(24px) scale(0.98); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        .icon-wrap {
            width: 76px;
            height: 76px;
            margin: 0 auto 20px;
            border-radius: 22px;
            background: linear-gradient(135deg, rgba(0, 242, 254, 0.15), rgba(157, 78, 221, 0.15));
            border: 1px solid rgba(255, 255, 255, 0.12);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 34px;
            box-shadow: 0 12px 24px -6px rgba(0, 242, 254, 0.2);
        }
        h1 { font-size: 24px; font-weight: 900; margin-bottom: 8px; letter-spacing: -0.5px; }
        p { font-size: 14px; color: var(--text-muted); line-height: 1.6; margin-bottom: 24px; }
        .hint-badge {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 12px 16px;
            border-radius: 14px;
            font-size: 13px;
            color: #cbd5e1;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="icon-wrap">🛡️</div>
        <h1>AI Pool Gateway</h1>
        <p>Access restricted and secured by encrypted device session.<br>Please request an authentication magic link via Telegram bot.</p>
        <div class="hint-badge">
            <span>🤖 Send /start to</span>
            <strong style="color: var(--accent-cyan);">@YJReportbot</strong>
        </div>
    </div>
</body>
</html>
"""

HTML_LOGS_TEMPLATE = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AI Pools • Analytics & Control</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&family=JetBrains+Mono:wght@500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #07090e;
            --card-glass: rgba(15, 20, 32, 0.65);
            --card-border: rgba(255, 255, 255, 0.08);
            --card-border-hover: rgba(255, 255, 255, 0.18);
            --accent-primary: #6366f1; /* Luxury Indigo */
            --accent-primary-glow: rgba(99, 102, 241, 0.25);
            --accent-cyan: #38bdf8;
            --accent-purple: #a855f7;
            --accent-emerald: #10b981;
            --text-main: #f8fafc;
            --text-secondary: #94a3b8;
            --text-tertiary: #64748b;
            --border-radius-xl: 22px;
            --border-radius-lg: 14px;
            --border-radius-md: 10px;
            --transition-smooth: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        body {
            background-color: #030712;
            color: var(--text-main);
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            min-height: 100vh;
            padding: 16px 14px 40px;
            line-height: 1.5;
            position: relative;
            overflow-x: hidden;
        }
        /* True luminous ambient spheres */
        .ambient-glow-1 {
            position: fixed;
            top: -100px;
            right: -80px;
            width: 320px;
            height: 320px;
            background: radial-gradient(circle, rgba(99, 102, 241, 0.28) 0%, rgba(99, 102, 241, 0) 70%);
            border-radius: 50%;
            pointer-events: none;
            z-index: 0;
            filter: blur(40px);
        }
        .ambient-glow-2 {
            position: fixed;
            top: 40%;
            left: -120px;
            width: 360px;
            height: 360px;
            background: radial-gradient(circle, rgba(168, 85, 247, 0.22) 0%, rgba(168, 85, 247, 0) 70%);
            border-radius: 50%;
            pointer-events: none;
            z-index: 0;
            filter: blur(50px);
        }
        .app-container {
            position: relative;
            z-index: 1;
            max-width: 580px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        /* Top Header - Pro Luxury Responsive */
        .top-navbar {
            background: rgba(15, 20, 32, 0.65);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-top: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: var(--border-radius-xl);
            padding: 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            box-shadow: 0 16px 36px -10px rgba(0,0,0,0.5);
            transition: var(--transition-smooth);
        }
        .header-main-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
        }
        .brand-section {
            display: flex;
            align-items: center;
            gap: 10px;
            min-width: 0;
        }
        .brand-logo {
            width: 38px;
            height: 38px;
            min-width: 38px;
            border-radius: 12px;
            background: linear-gradient(135deg, #0ea5e9, #8b5cf6);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 19px;
            box-shadow: 0 6px 14px -3px rgba(14, 165, 233, 0.4);
            border: 1px solid rgba(255,255,255,0.2);
            flex-shrink: 0;
        }
        .brand-title {
            font-size: 15px;
            font-weight: 800;
            letter-spacing: -0.2px;
            display: flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
        }
        .live-pill {
            background: rgba(14, 165, 233, 0.12);
            border: 1px solid rgba(14, 165, 233, 0.3);
            color: var(--accent-cyan);
            font-size: 9px;
            font-weight: 800;
            padding: 2px 6px;
            border-radius: 10px;
            letter-spacing: 0.5px;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            line-height: 1;
        }
        .live-dot {
            width: 5px;
            height: 5px;
            border-radius: 50%;
            background: var(--accent-cyan);
            box-shadow: 0 0 6px var(--accent-cyan);
            animation: pulseGlow 2s infinite ease-in-out;
        }
        @keyframes pulseGlow {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.8); }
        }
        .settings-top-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.12);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: 14px;
            font-size: 12px;
            font-weight: 700;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
            transition: var(--transition-smooth);
            font-family: inherit;
            flex-shrink: 0;
        }
        .settings-top-btn:hover {
            background: rgba(255, 255, 255, 0.12);
            border-color: rgba(255, 255, 255, 0.25);
            transform: translateY(-1px);
        }
        .settings-top-btn.active {
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.35), rgba(168, 85, 247, 0.35));
            border-color: rgba(168, 85, 247, 0.6);
            color: #fff;
            box-shadow: 0 4px 12px rgba(168, 85, 247, 0.25);
        }
        .header-sub-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
            padding-top: 6px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            font-size: 11px;
        }
        .brand-subtitle {
            color: var(--text-tertiary);
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .version-badge {
            background: rgba(99, 102, 241, 0.1);
            border: 1px solid rgba(99, 102, 241, 0.2);
            color: #818cf8;
            padding: 1px 6px;
            border-radius: 6px;
            font-size: 10px;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
            letter-spacing: 0.2px;
            line-height: 1.2;
        }

        /* Provider Segmented Pill Selector */
        .provider-segmented-wrap {
            background: rgba(11, 15, 25, 0.8);
            border: 1px solid var(--card-border);
            border-radius: 18px;
            padding: 4px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.4);
        }
        .provider-tab-btn {
            width: 100%;
            background: transparent;
            border: none;
            color: var(--text-secondary);
            padding: 10px 14px;
            border-radius: 14px;
            font-size: 13px;
            font-weight: 800;
            font-family: 'Cairo', sans-serif;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: var(--transition-smooth);
            position: relative;
        }
        .provider-tab-btn.active {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.03));
            color: #fff;
            border: 1px solid rgba(255, 255, 255, 0.15);
            box-shadow: 0 8px 20px -6px rgba(0,0,0,0.6);
        }
        .provider-tab-btn.active.codex-theme {
            background: linear-gradient(135deg, rgba(139, 92, 246, 0.25), rgba(139, 92, 246, 0.1));
            border-color: rgba(139, 92, 246, 0.4);
            color: #d8b4fe;
            box-shadow: 0 8px 20px -6px rgba(139, 92, 246, 0.3);
        }
        .provider-tab-btn.active.antigravity-theme {
            background: linear-gradient(135deg, rgba(6, 182, 212, 0.25), rgba(6, 182, 212, 0.1));
            border-color: rgba(6, 182, 212, 0.4);
            color: #67e8f9;
            box-shadow: 0 8px 20px -6px rgba(6, 182, 212, 0.3);
        }

        /* Glass Cards */
        .glass-card {
            background: rgba(255, 255, 255, 0.035);
            backdrop-filter: blur(28px);
            -webkit-backdrop-filter: blur(28px);
            border: 1px solid rgba(255, 255, 255, 0.09);
            border-top: 1px solid rgba(255, 255, 255, 0.22);
            border-radius: var(--border-radius-xl);
            padding: 22px 18px;
            box-shadow: 0 20px 48px -12px rgba(0,0,0,0.7), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            transition: var(--transition-smooth);
            position: relative;
            overflow: hidden;
        }
        .glass-card:hover {
            border-color: var(--card-border-hover);
        }
        .glass-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 1px;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.12), transparent);
        }

        /* Pool Header Inside Card */
        .pool-header-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 14px;
        }
        .pool-title-group {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .pool-title {
            font-size: 15px;
            font-weight: 800;
        }
        .pool-badge {
            padding: 3px 8px;
            border-radius: 8px;
            font-size: 10px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
        }

        /* CUSTOM LUXURY ANIMATED DROPDOWN */
        .custom-select-wrapper {
            position: relative;
            width: 100%;
            margin-bottom: 16px;
            user-select: none;
            z-index: 50;
        }
        .custom-select-trigger {
            background: rgba(11, 15, 25, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: var(--border-radius-lg);
            padding: 12px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            transition: var(--transition-smooth);
            box-shadow: 0 4px 14px rgba(0,0,0,0.3);
        }
        .custom-select-trigger:hover, .custom-select-wrapper.open .custom-select-trigger {
            border-color: var(--accent-cyan);
            box-shadow: 0 0 0 3px var(--accent-cyan-glow);
        }
        .custom-select-trigger .selected-model-text {
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            font-weight: 600;
            color: #f3f4f6;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .custom-select-trigger .chevron-icon {
            font-size: 12px;
            color: var(--text-secondary);
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .custom-select-wrapper.open .chevron-icon {
            transform: rotate(180deg);
            color: var(--accent-cyan);
        }
        .custom-select-options {
            position: absolute;
            top: calc(100% + 6px);
            left: 0;
            right: 0;
            background: #0d1322;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: var(--border-radius-lg);
            overflow: hidden;
            box-shadow: 0 20px 40px -8px rgba(0,0,0,0.8), 0 0 0 1px rgba(255,255,255,0.05);
            opacity: 0;
            visibility: hidden;
            transform: translateY(-8px) scale(0.98);
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            max-height: 260px;
            overflow-y: auto;
            backdrop-filter: blur(24px);
        }
        .custom-select-wrapper.open .custom-select-options {
            opacity: 1;
            visibility: visible;
            transform: translateY(0) scale(1);
        }
        .custom-option {
            padding: 12px 16px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: #d1d5db;
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            transition: background 0.15s ease, color 0.15s ease;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        }
        .custom-option:last-child { border-bottom: none; }
        .custom-option:hover {
            background: rgba(6, 182, 212, 0.12);
            color: #fff;
        }
        .custom-option.selected {
            background: rgba(6, 182, 212, 0.2);
            color: var(--accent-cyan);
            font-weight: 700;
        }

        /* Model Stats 3-Grid */
        .stats-tri-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 16px;
        }
        .stat-tri-card {
            background: rgba(11, 15, 25, 0.6);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: var(--border-radius-md);
            padding: 12px 10px;
            text-align: center;
            transition: var(--transition-smooth);
        }
        .stat-tri-card:hover {
            background: rgba(255, 255, 255, 0.03);
            border-color: rgba(255, 255, 255, 0.1);
        }
        .stat-tri-label {
            font-size: 11px;
            color: var(--text-secondary);
            margin-bottom: 4px;
            font-weight: 600;
        }
        .stat-tri-val {
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            font-weight: 800;
            color: #fff;
            letter-spacing: -0.3px;
        }

        /* Accounts Strip */
        .accounts-strip {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .account-item-pill {
            background: rgba(15, 20, 32, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: var(--border-radius-md);
            padding: 14px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            direction: ltr; /* Strict Left to Right: Email/Dot on Left, Metrics on Right */
            transition: var(--transition-smooth);
        }
        .account-item-pill.active {
            border-color: rgba(16, 185, 129, 0.35);
            background: rgba(16, 185, 129, 0.08);
            box-shadow: inset 0 0 16px rgba(16, 185, 129, 0.06);
        }
        .account-actions {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .account-normal-content {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
            gap: 12px;
        }
        .account-actions-bar {
            display: none !important;
            width: 100%;
            gap: 10px;
            align-items: center;
            justify-content: stretch;
        }
        .account-item-pill.selected .account-normal-content {
            display: none !important;
        }
        .account-item-pill.selected .account-actions-bar {
            display: flex !important;
        }
        .account-btn-switch, .account-btn-delete {
            flex: 1 1 50%;
            min-height: 38px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 700;
            font-family: inherit;
            letter-spacing: 0.3px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            cursor: pointer;
            transition: all 0.25s ease;
            box-sizing: border-box;
            border: 1px solid transparent;
        }
        .account-btn-switch {
            background: linear-gradient(135deg, rgba(14, 165, 233, 0.2), rgba(2, 132, 199, 0.3));
            border-color: rgba(56, 189, 248, 0.45);
            color: #38bdf8;
        }
        .account-btn-switch:not(:disabled):hover {
            background: linear-gradient(135deg, #0ea5e9, #0284c7);
            color: #fff;
            box-shadow: 0 4px 12px rgba(14, 165, 233, 0.35);
            transform: translateY(-1px);
        }
        .account-btn-delete {
            background: linear-gradient(135deg, rgba(239, 68, 68, 0.2), rgba(220, 38, 38, 0.3));
            border-color: rgba(239, 68, 68, 0.45);
            color: #f87171;
        }
        .account-btn-delete:not(:disabled):hover {
            background: linear-gradient(135deg, #ef4444, #dc2626);
            color: #fff;
            box-shadow: 0 4px 12px rgba(239, 68, 68, 0.35);
            transform: translateY(-1px);
        }
        .account-btn-switch:disabled, .account-btn-delete:disabled {
            opacity: 0.45;
            cursor: not-allowed;
            transform: none;
            filter: grayscale(0.6);
        }
        .account-switch-btn {
            display: none !important;
        }
        .account-item-pill.selected {
            border-color: rgba(56, 189, 248, 0.45);
            background: rgba(56, 189, 248, 0.06);
            box-shadow: 0 0 14px rgba(56, 189, 248, 0.12);
        }
        .acc-info-left {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .acc-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--text-tertiary);
        }
        .acc-dot.active {
            background: var(--accent-emerald);
            box-shadow: 0 0 8px var(--accent-emerald);
        }
        .acc-email {
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: #e5e7eb;
        }
        .acc-metrics {
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            font-weight: 700;
            color: var(--text-secondary);
        }

        /* Activity Stream Header & Items */
        .stream-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 12px;
        }
        .stream-title-text {
            font-size: 14px;
            font-weight: 800;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .stream-badge {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 11px;
            color: var(--text-secondary);
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-weight: 600;
        }
        .stream-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
            max-height: 520px;
            overflow-y: auto;
            padding-right: 4px;
        }
        .stream-list::-webkit-scrollbar { width: 4px; }
        .stream-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }
        
        /* Activity Stream Header & Items (Approved Luxury Cards) */
        .stream-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 14px;
        }
        .stream-title-text {
            font-size: 14px;
            font-weight: 800;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .stream-badge {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 11px;
            color: var(--text-secondary);
            font-family: 'Plus Jakarta Sans', sans-serif;
            font-weight: 600;
        }
        .stream-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
            max-height: 520px;
            overflow-y: auto;
            padding-right: 4px;
        }
        .stream-list::-webkit-scrollbar { width: 4px; }
        .stream-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }

        .stream-card-item {
            background: rgba(15, 20, 32, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: var(--border-radius-md);
            padding: 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 10px;
            direction: ltr; /* Strict Left to Right */
            text-align: left;
            transition: var(--transition-smooth);
            animation: fadeIn 0.3s ease;
        }
        .stream-card-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            direction: ltr;
        }
        .stream-card-bottom {
            display: flex;
            align-items: center;
            justify-content: space-between;
            direction: ltr;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            border-top: 1px solid rgba(255, 255, 255, 0.04);
            padding-top: 8px;
        }
        .stream-tok-badge {
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: 8px;
            letter-spacing: -0.3px;
            white-space: nowrap;
        }
        .stream-tok-badge.codex {
            background: rgba(139, 92, 246, 0.15);
            color: #c084fc;
            border: 1px solid rgba(139, 92, 246, 0.3);
        }
        .stream-tok-badge.antigravity {
            background: rgba(6, 182, 212, 0.15);
            color: #67e8f9;
            border: 1px solid rgba(6, 182, 212, 0.3);
        }
        .stream-model-name {
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            font-weight: 700;
            color: #fff;
            display: flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
            flex-shrink: 0;
        }
        .stream-time-text {
            font-size: 10px;
            color: var(--text-tertiary);
            font-family: 'JetBrains Mono', monospace;
        }
        .stream-tok-badge {
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: 8px;
            letter-spacing: -0.3px;
        }
        .stream-tok-badge.codex {
            background: rgba(139, 92, 246, 0.15);
            color: #c084fc;
            border: 1px solid rgba(139, 92, 246, 0.3);
        }
        .stream-tok-badge.antigravity {
            background: rgba(6, 182, 212, 0.15);
            color: #67e8f9;
            border: 1px solid rgba(6, 182, 212, 0.3);
        }

        /* Refresh Interactive Button (High-End Fintech/SaaS Glassmorphism) */
        .refresh-btn {
            position: relative;
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.02) 100%);
            border: 1px solid rgba(255, 255, 255, 0.14);
            color: #f1f5f9;
            border-radius: 9px;
            padding: 5px 11px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.15);
            transition: all 0.22s cubic-bezier(0.2, 0.8, 0.2, 1);
            font-family: inherit;
            letter-spacing: -0.1px;
        }
        .refresh-btn:hover {
            color: #ffffff;
            border-color: rgba(56, 189, 248, 0.5);
            background: linear-gradient(180deg, rgba(56, 189, 248, 0.15) 0%, rgba(56, 189, 248, 0.05) 100%);
            transform: translateY(-1px);
            box-shadow: 0 4px 14px rgba(14, 165, 233, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.25);
        }
        .refresh-btn:active {
            transform: translateY(0);
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
        }
        .refresh-icon-wrap {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 14px;
            height: 14px;
        }
        .refresh-svg {
            display: block;
            transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        .refresh-btn:hover .refresh-svg {
            transform: rotate(45deg);
        }
        .refresh-btn.refreshing {
            background: linear-gradient(180deg, rgba(14, 165, 233, 0.2) 0%, rgba(14, 165, 233, 0.08) 100%) !important;
            border-color: #38bdf8 !important;
            color: #38bdf8 !important;
            box-shadow: 0 0 16px rgba(56, 189, 248, 0.35) !important;
            cursor: wait;
        }
        .refresh-btn.refreshing .refresh-svg {
            animation: spinRefreshSvg 0.75s linear infinite;
        }
        @keyframes spinRefreshSvg {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        .refresh-btn.refresh-success {
            background: linear-gradient(180deg, rgba(16, 185, 129, 0.22) 0%, rgba(16, 185, 129, 0.08) 100%) !important;
            border-color: #10b981 !important;
            color: #34d399 !important;
            box-shadow: 0 0 16px rgba(16, 185, 129, 0.4) !important;
            animation: refreshSuccessPop 0.35s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        }
        @keyframes refreshSuccessPop {
            0% { transform: scale(0.95); }
            50% { transform: scale(1.04); }
            100% { transform: scale(1); }
        }

        /* Accounts Toggle Button */
        .accounts-toggle-btn {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            color: var(--text-secondary);
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.25s ease;
            font-family: inherit;
        }
        .accounts-toggle-btn:hover {
            background: rgba(255, 255, 255, 0.09);
            color: #fff;
            border-color: var(--accent-cyan);
            transform: translateY(-1px);
        }
        .accounts-strip.collapsed {
            display: none !important;
        }

        /* Toast notification */
        #toast-notification {
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%) translateY(-60px);
            background: rgba(16, 185, 129, 0.95);
            backdrop-filter: blur(12px);
            color: #fff;
            padding: 8px 18px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 700;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4), 0 0 16px rgba(16, 185, 129, 0.3);
            z-index: 9999;
            opacity: 0;
            transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
            pointer-events: none;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        #toast-notification.show {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }
        .refresh-btn svg { width: 14px; height: 14px; transition: transform 0.4s ease; }
        .refresh-btn.rotating svg { transform: rotate(360deg); }
        .refresh-btn:hover svg { transform: rotate(180deg); }

        /* Unified Badges */
        .badge {
            white-space: nowrap !important;
            flex-shrink: 0;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 8px;
            letter-spacing: 0.2px;
            background: rgba(255, 255, 255, 0.06);
            color: #94a3b8;
        }

        /* Settings Card Structure */
        .settings-card {
            padding: 16px;
        }
        .settings-card-header {
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-bottom: 14px;
            width: 100%;
        }
        .settings-card-top-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            width: 100%;
            gap: 10px;
        }
        .settings-card-title-group {
            display: flex;
            gap: 10px;
            align-items: center;
            min-width: 0;
        }
        .settings-card-icon {
            font-size: 24px;
            flex-shrink: 0;
        }
        .settings-card-title {
            font-size: 15px;
            font-weight: 700;
            color: #fff;
            white-space: nowrap;
        }
        .settings-card-sub {
            font-size: 12px;
            color: var(--text-secondary);
            line-height: 1.4;
            padding-left: 34px;
        }
        .settings-info-box {
            background: rgba(0, 0, 0, 0.3);
            border-radius: 8px;
            padding: 10px 12px;
            font-size: 12px;
            color: #cbd5e1;
            margin-bottom: 12px;
            line-height: 1.6;
            overflow-wrap: anywhere;
        }
        .recheck-btn {
            white-space: nowrap !important;
            flex-shrink: 0;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.12);
            color: #cbd5e1;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 600;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .recheck-btn:hover {
            background: rgba(56, 189, 248, 0.15);
            border-color: rgba(56, 189, 248, 0.35);
            color: #38bdf8;
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }
        .bento-update-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-bottom: 12px;
        }
        .bento-update-cell {
            background: rgba(0, 0, 0, 0.35);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 10px;
            padding: 10px 12px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .bento-cell-label {
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 0.5px;
            color: var(--text-tertiary);
            text-transform: uppercase;
        }
        .bento-cell-value {
            font-size: 12px;
            color: #f1f5f9;
            font-weight: 600;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .commit-log-box {
            background: rgba(0, 0, 0, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-left: 3px solid #6366f1;
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 11px;
            color: #94a3b8;
            line-height: 1.5;
            margin-bottom: 12px;
        }
        .update-cta-btn {
            width: 100%;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.18), rgba(168, 85, 247, 0.18));
            border: 1px solid rgba(168, 85, 247, 0.4);
            color: #e0e7ff;
            padding: 11px 16px;
            border-radius: 12px;
            font-weight: 700;
            font-size: 13px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            position: relative;
            overflow: hidden;
        }
        .update-cta-btn:hover {
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.3), rgba(168, 85, 247, 0.3));
            border-color: rgba(168, 85, 247, 0.7);
            color: #ffffff;
            transform: translateY(-1px);
            box-shadow: 0 6px 20px rgba(168, 85, 247, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.2);
        }
        .update-cta-btn.updating {
            background: linear-gradient(135deg, rgba(14, 165, 233, 0.2), rgba(99, 102, 241, 0.25)) !important;
            border-color: #38bdf8 !important;
            color: #38bdf8 !important;
            box-shadow: 0 0 20px rgba(56, 189, 248, 0.3) !important;
            cursor: wait;
            transform: scale(0.99);
        }
        .update-cta-btn.update-success {
            background: linear-gradient(135deg, rgba(16, 185, 129, 0.25), rgba(5, 150, 105, 0.25)) !important;
            border-color: #10b981 !important;
            color: #34d399 !important;
            box-shadow: 0 0 22px rgba(16, 185, 129, 0.4) !important;
            transform: scale(1.01);
            animation: pulseGlowSuccess 1.5s ease-in-out infinite;
        }
        .update-cta-btn.update-error {
            background: rgba(239, 68, 68, 0.2) !important;
            border-color: #ef4444 !important;
            color: #f87171 !important;
            box-shadow: 0 0 15px rgba(239, 68, 68, 0.3) !important;
        }
        @keyframes pulseGlowSuccess {
            0%, 100% { box-shadow: 0 0 14px rgba(16, 185, 129, 0.3); }
            50% { box-shadow: 0 0 25px rgba(16, 185, 129, 0.6); }
        }
        .update-cta-btn:disabled {
            opacity: 0.85;
            cursor: wait;
        }
        /* Creative Animated Bouncing Dots & Loaders */
        .bouncing-dots {
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .bouncing-dots span {
            width: 5px;
            height: 5px;
            background-color: currentColor;
            border-radius: 50%;
            display: inline-block;
            animation: dotBounce 1.4s infinite ease-in-out both;
        }
        .bouncing-dots span:nth-child(1) { animation-delay: -0.32s; }
        .bouncing-dots span:nth-child(2) { animation-delay: -0.16s; }
        .bouncing-dots span:nth-child(3) { animation-delay: 0s; }
        @keyframes dotBounce {
            0%, 80%, 100% { transform: scale(0.2); opacity: 0.3; }
            40% { transform: scale(1.1); opacity: 1; }
        }
        .spin-icon {
            display: inline-block;
            animation: spinAnim 1s linear infinite;
        }
        @keyframes spinAnim {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        /* Secondary Check Updates Action Button */
        .check-update-btn {
            background: rgba(56, 189, 248, 0.1);
            border: 1px solid rgba(56, 189, 248, 0.25);
            color: #38bdf8;
            padding: 10px 14px;
            border-radius: 10px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            transition: all 0.25s ease;
        }
        .check-update-btn:hover {
            background: rgba(56, 189, 248, 0.2);
            border-color: rgba(56, 189, 248, 0.5);
            transform: translateY(-1px);
        }
        .check-update-btn:disabled {
            opacity: 0.6;
            cursor: wait;
            transform: none;
        }
        .update-action-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-top: 4px;
        }
        @media (max-width: 640px) {
            .update-action-row {
                grid-template-columns: 1fr;
                gap: 8px;
            }
        }
        .cli-tool-item {
            background: rgba(0, 0, 0, 0.25);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
            padding: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
        }
        .agent-config-detail-box {
            font-size: 11px;
            font-family: 'JetBrains Mono', monospace;
            overflow-wrap: anywhere;
            color: #94a3b8;
            margin-top: 10px;
            padding: 8px 10px;
            background: rgba(0, 0, 0, 0.28);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 6px;
            line-height: 1.5;
        }

        /* Responsive Mobile Styles */
        @media (max-width: 640px) {
            .app-container {
                padding: 12px 10px 40px 10px;
                gap: 12px;
            }
            .glass-card {
                padding: 16px 14px !important;
                border-radius: 14px;
            }
            .top-navbar {
                padding: 14px 14px 10px 14px;
            }
            .header-sub-row {
                flex-direction: row !important;
                align-items: center !important;
                justify-content: space-between !important;
                gap: 8px;
            }
            .brand-subtitle {
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                font-size: 11px;
                line-height: 1.2;
            }
            .version-badge {
                align-self: center !important;
                flex-shrink: 0;
            }
            .provider-tab-btn {
                font-size: 13px;
                padding: 8px 10px;
            }
            .settings-card-title {
                font-size: 14px;
            }
            .settings-card-sub {
                padding-left: 0;
                font-size: 11px;
            }
            .settings-card-top-row {
                gap: 8px;
            }
            .bento-update-grid {
                grid-template-columns: 1fr;
                gap: 8px;
            }
            .cli-tool-item {
                flex-direction: column;
                align-items: stretch;
                gap: 10px;
            }
            .cli-tool-item button {
                width: 100%;
            }
            .account-item-pill {
                flex-direction: column;
                align-items: stretch;
                gap: 8px;
            }
            .account-actions {
                justify-content: space-between;
                width: 100%;
            }
        }

        /* Delete Confirmation Modal */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(8px);
            z-index: 10000;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }
        .modal-dialog {
            width: 100%;
            max-width: 420px;
            background: rgba(15, 23, 42, 0.96);
            border: 1px solid rgba(239, 68, 68, 0.35);
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6), 0 0 24px rgba(239, 68, 68, 0.15);
            border-radius: 16px;
            padding: 22px;
            box-sizing: border-box;
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
        }
        .modal-close-btn {
            background: none;
            border: none;
            color: #94a3b8;
            font-size: 22px;
            cursor: pointer;
            line-height: 1;
            padding: 0 4px;
        }
        .modal-close-btn:hover {
            color: #fff;
        }
        .modal-input {
            width: 100%;
            background: rgba(0, 0, 0, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 8px;
            padding: 11px 14px;
            color: #fff;
            font-size: 13px;
            font-family: inherit;
            box-sizing: border-box;
            outline: none;
            transition: var(--transition-smooth);
        }
        .modal-input:focus {
            border-color: #ef4444;
            box-shadow: 0 0 12px rgba(239, 68, 68, 0.3);
        }
        .modal-actions {
            display: flex;
            gap: 10px;
            margin-top: 18px;
        }
        .modal-btn-cancel {
            flex: 1;
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #cbd5e1;
            border-radius: 8px;
            padding: 10px;
            font-weight: 600;
            font-size: 13px;
            cursor: pointer;
            transition: var(--transition-smooth);
        }
        .modal-btn-cancel:hover {
            background: rgba(255, 255, 255, 0.15);
            color: #fff;
        }
        .modal-btn-delete {
            flex: 1;
            background: linear-gradient(135deg, #ef4444, #dc2626);
            border: none;
            color: #fff;
            border-radius: 8px;
            padding: 10px;
            font-weight: 700;
            font-size: 13px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            transition: var(--transition-smooth);
        }
        .modal-btn-delete:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            filter: grayscale(0.8);
        }
        .modal-btn-delete:not(:disabled):hover {
            box-shadow: 0 4px 14px rgba(239, 68, 68, 0.4);
            transform: translateY(-1px);
        }
    </style>
</head>
<body>
    <div class="ambient-glow-1"></div>
    <div class="ambient-glow-2"></div>
    <div class="app-container">
        <div id="toast-notification">
            <span id="toast-text">Logs refreshed successfully</span>
        </div>
        <!-- Delete Account Confirmation Modal -->
        <div id="delete-modal-overlay" class="modal-overlay" style="display: none;" onclick="closeDeleteModal(event)">
            <div class="modal-dialog glass-card" onclick="event.stopPropagation()">
                <div class="modal-header">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 20px;">🗑️</span>
                        <span style="font-weight: 700; color: #fff; font-size: 15px;">Confirm Account Deletion</span>
                    </div>
                    <button class="modal-close-btn" onclick="closeDeleteModal()">&times;</button>
                </div>
                <div class="modal-body">
                    <p style="color: #cbd5e1; font-size: 13px; line-height: 1.5; margin-bottom: 12px;">
                        Are you sure you want to delete <strong id="modal-del-account-label" style="color: #fff;">Account</strong>?
                    </p>
                    <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 12px; font-size: 12px; color: #fca5a5; margin-bottom: 14px;">
                        ⚠️ This will permanently delete and purge the account credentials completely. Type <strong style="color: #ef4444; font-family: monospace;">confirm</strong> below to proceed:
                    </div>
                    <input type="text" id="modal-delete-confirm-input" class="modal-input" placeholder="Type confirm" autocomplete="off" oninput="validateDeleteInput()" />
                </div>
                <div class="modal-actions">
                    <button type="button" class="modal-btn-cancel" onclick="closeDeleteModal()">Cancel</button>
                    <button type="button" id="modal-btn-confirm-delete" class="modal-btn-delete" disabled onclick="executeAccountDelete()">
                        🗑️ Permanent Delete
                    </button>
                </div>
            </div>
        </div>
        <!-- Top Navigation Header -->
        <header class="top-navbar">
            <div class="header-main-row">
                <div class="brand-section">
                    <div class="brand-logo">⚡</div>
                    <div class="brand-title">
                        <span>AI Pool Guardian</span>
                        <span class="live-pill"><span class="live-dot"></span>LIVE</span>
                    </div>
                </div>
                <button id="settings-toggle-btn" class="settings-top-btn" onclick="toggleSettingsView()">
                    <span id="settings-btn-icon">⚙️</span>
                    <span id="settings-btn-text">Settings</span>
                </button>
            </div>
            <div class="header-sub-row">
                <div class="brand-subtitle">Autonomous Failover Architecture</div>
                <span class="version-badge" id="system-version-pill" title="Active Release Version">v1.9.2</span>
            </div>
        </header>

        <!-- Provider Tab Bar -->
        <div class="provider-segmented-wrap" id="provider-segmented-bar">
            <button class="provider-tab-btn active codex-theme" id="tab-codex" onclick="switchProvider('Codex')">
                <span>🟣</span>
                <span>ChatGPT</span>
            </button>
            <button class="provider-tab-btn antigravity-theme" id="tab-antigravity" onclick="switchProvider('Antigravity')">
                <span>🟢</span>
                <span>Gemini</span>
            </button>
        </div>

        <!-- Model Deep-Dive Analytics Card -->
        <section class="glass-card" id="pool-overview-section">
            <div class="pool-header-row">
                <div class="pool-title-group">
                    <span id="pool-icon" style="font-size: 18px;">🟣</span>
                    <span class="pool-title" id="pool-title-label">ChatGPT Pool</span>
                </div>
                <button class="refresh-btn" id="main-refresh-btn" onclick="fetchLiveLogs(true)" title="Sync real-time metrics and stream">
                    <span class="refresh-icon-wrap" id="main-refresh-icon-wrap">
                        <svg class="refresh-svg" id="main-refresh-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                        </svg>
                    </span>
                    <span id="main-refresh-text">Sync Live</span>
                </button>
            </div>

            <!-- Pool Overall Capacity Card -->
            <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.07); border-radius: var(--border-radius-md); padding: 12px 14px; margin-bottom: 16px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 8px;">
                    <span style="font-size: 12px; font-weight: 700; color: var(--text-secondary);">📊 Pool Capacity:</span>
                    <span id="pool-total-capacity-text" style="font-family:'JetBrains Mono',monospace; font-size:11px; font-weight:700; color:var(--accent-cyan);">--</span>
                </div>
                <!-- 5H & Weekly Bar -->
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                    <div>
                        <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--text-tertiary); margin-bottom:3px;">
                            <span>5-Hour</span>
                            <span id="pool-5h-summary-val" style="font-family:'JetBrains Mono'; font-weight:700; color:#fff;">--%</span>
                        </div>
                        <div style="height:6px; background:rgba(255,255,255,0.06); border-radius:3px; overflow:hidden;">
                            <div id="pool-5h-progress" style="height:100%; width:0%; background:linear-gradient(90deg, #6366f1, #38bdf8); transition:width 0.4s ease;"></div>
                        </div>
                    </div>
                    <div>
                        <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--text-tertiary); margin-bottom:3px;">
                            <span>Weekly</span>
                            <span id="pool-wk-summary-val" style="font-family:'JetBrains Mono'; font-weight:700; color:#fff;">--%</span>
                        </div>
                        <div style="height:6px; background:rgba(255,255,255,0.06); border-radius:3px; overflow:hidden;">
                            <div id="pool-wk-progress" style="height:100%; width:0%; background:linear-gradient(90deg, #8b5cf6, #10b981); transition:width 0.4s ease;"></div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 3-Metrics Grid for Overall Provider Consumption -->
            <div class="stats-tri-grid">
                <div class="stat-tri-card">
                    <div class="stat-tri-label">Total Tokens</div>
                    <div class="stat-tri-val" id="model-total-tokens" style="color: var(--accent-cyan);">--</div>
                </div>
                <div class="stat-tri-card">
                    <div class="stat-tri-label">Total Requests</div>
                    <div class="stat-tri-val" id="model-total-requests" style="color: var(--accent-purple);">--</div>
                </div>
                <div class="stat-tri-card">
                    <div class="stat-tri-label">Avg / Req</div>
                    <div class="stat-tri-val" id="model-avg-tokens" style="color: var(--accent-emerald);">--</div>
                </div>
            </div>

            <!-- Pool Accounts Summary List -->
            <div style="margin-top: 14px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 8px;">
                    <div style="font-size: 12px; font-weight: 700; color: var(--text-secondary);">Connected Accounts:</div>
                    <button id="accounts-toggle-btn" class="accounts-toggle-btn" onclick="toggleAccountsList()">
                        <span id="accounts-toggle-icon">👁️</span>
                        <span id="accounts-toggle-text">View Accounts</span>
                        <span id="accounts-toggle-chevron" style="transition: transform 0.3s ease; display: inline-block; font-size: 10px; transform: rotate(180deg);">▾</span>
                    </button>
                </div>
                <div class="accounts-strip collapsed" id="pool-accounts-container">
                    <!-- Loaded dynamically -->
                </div>
            </div>
        </section>

        <!-- Real Stream Card -->
        <section class="glass-card" id="stream-section">
            <div class="stream-header">
                <div class="stream-title-text">
                    <span>⚡</span>
                    <span>Live Operations Stream</span>
                </div>
            </div>
            <div class="stream-list" id="activity-stream-list">
                <!-- Activity Items Loaded Here -->
            </div>
        </section>

        <!-- Settings View Section -->
        <section id="settings-view-section" style="display: none; flex-direction: column; gap: 14px;">
            <!-- Hermes Agent Integration Card -->
            <div class="glass-card settings-card">
                <div class="settings-card-header">
                    <div class="settings-card-top-row">
                        <div class="settings-card-title-group">
                            <span class="settings-card-icon">🤖</span>
                            <span class="settings-card-title">Hermes Agent Integration</span>
                        </div>
                        <span id="hermes-badge" class="badge">Checking...</span>
                    </div>
                    <div class="settings-card-sub">Auto-link pool gateways in ~/.hermes/config.yaml</div>
                </div>
                <div class="settings-info-box">
                    Config Path: <code style="color: var(--accent-cyan);">~/.hermes/config.yaml</code><br>
                    Models: <span style="color: var(--accent-emerald);">gemini-3.8-flash (1M)</span>, <span style="color: var(--accent-purple);">gpt-6-astra (1M)</span> + full family
                </div>
                <div style="display: flex; gap: 8px;">
                    <button class="action-btn" id="btn-sync-hermes" onclick="triggerIntegration('hermes')" style="flex: 1; background: linear-gradient(135deg, #0ea5e9, #0284c7); color: #fff; border: none; padding: 10px 14px; border-radius: 8px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px;">
                        <span>⚡</span>
                        <span>Configure Hermes Agent</span>
                    </button>
                </div>
            </div>

            <!-- OpenClaw Integration Card -->
            <div class="glass-card settings-card">
                <div class="settings-card-header">
                    <div class="settings-card-top-row">
                        <div class="settings-card-title-group">
                            <span class="settings-card-icon">🦅</span>
                            <span class="settings-card-title">OpenClaw Integration</span>
                        </div>
                        <span id="openclaw-badge" class="badge">Checking...</span>
                    </div>
                    <div class="settings-card-sub">Auto-link gemini_pool & codex_pool in ~/.openclaw/openclaw.json</div>
                </div>
                <div class="settings-info-box">
                    Config Path: <code style="color: var(--accent-cyan);">~/.openclaw/openclaw.json</code><br>
                    Providers: <span style="color: var(--accent-emerald);">gemini_pool</span>, <span style="color: var(--accent-purple);">codex_pool</span>
                </div>
                <div style="display: flex; gap: 8px;">
                    <button class="action-btn" id="btn-sync-openclaw" onclick="triggerIntegration('openclaw')" style="flex: 1; background: linear-gradient(135deg, #8b5cf6, #7c3aed); color: #fff; border: none; padding: 10px 14px; border-radius: 8px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px;">
                        <span>⚡</span>
                        <span>Configure OpenClaw</span>
                    </button>
                </div>
            </div>

            <!-- CLI Tools Prerequisites Card -->
            <div class="glass-card settings-card">
                <div class="settings-card-header">
                    <div class="settings-card-top-row">
                        <div class="settings-card-title-group">
                            <span class="settings-card-icon">🛠️</span>
                            <span class="settings-card-title">CLI Tools Status</span>
                        </div>
                        <button class="recheck-btn" onclick="fetchCliToolsStatus()" title="Re-check installed CLI tools">
                            🔄 Re-check
                        </button>
                    </div>
                    <div class="settings-card-sub">Detects and configures CLI tools required for enrolling accounts.</div>
                </div>

                <div style="display: flex; flex-direction: column; gap: 10px;">
                    <!-- Codex CLI item -->
                    <div class="cli-tool-item">
                        <div>
                            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                                <span style="font-weight: 700; color: #fff; font-size: 13px;">OpenAI Codex CLI</span>
                                <span id="codex-cli-badge" class="badge">Checking...</span>
                            </div>
                            <div id="codex-cli-desc" style="font-size: 11px; color: var(--text-secondary); margin-top: 4px;">Required for 'c add' interactive login session.</div>
                        </div>
                        <button id="btn-install-codex" class="action-btn" onclick="installCliTool('codex')" style="display: none; background: linear-gradient(135deg, #8b5cf6, #7c3aed); color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-size: 12px; font-weight: 700; cursor: pointer; white-space: nowrap;">
                            ⚡ Install CLI
                        </button>
                    </div>

                    <!-- Antigravity CLI item -->
                    <div class="cli-tool-item">
                        <div>
                            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                                <span style="font-weight: 700; color: #fff; font-size: 13px;">Gemini Antigravity CLI</span>
                                <span id="antigravity-cli-badge" class="badge">Checking...</span>
                            </div>
                            <div id="antigravity-cli-desc" style="font-size: 11px; color: var(--text-secondary); margin-top: 4px;">Required for 'ag add' interactive login session.</div>
                        </div>
                        <button id="btn-install-antigravity" class="action-btn" onclick="installCliTool('antigravity')" style="display: none; background: linear-gradient(135deg, #10b981, #059669); color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-size: 12px; font-weight: 700; cursor: pointer; white-space: nowrap;">
                            ⚡ Setup CLI
                        </button>
                    </div>
                </div>
            </div>

            <!-- System Updates Card -->
            <div class="glass-card settings-card">
                <div class="settings-card-header">
                    <div class="settings-card-top-row">
                        <div class="settings-card-title-group">
                            <span class="settings-card-icon">🚀</span>
                            <span class="settings-card-title">System Updates</span>
                        </div>
                        <span id="update-status-pill" class="badge" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3);">
                            Live Synchronized
                        </span>
                    </div>
                    <div class="settings-card-sub">Continuous release delivery with live verified deployment.</div>
                </div>

                <!-- Bento Grid Info -->
                <div class="bento-update-grid">
                    <div class="bento-update-cell">
                        <span class="bento-cell-label">Running Build</span>
                        <div class="bento-cell-value">
                            <span style="color: #34d399; font-weight: 700;" id="settings-installed-version">Loading…</span>
                        </div>
                    </div>
                    <div class="bento-update-cell">
                        <span class="bento-cell-label">Source Repo</span>
                        <div class="bento-cell-value">
                            <span style="color: #38bdf8;">🐙 yjlvfe/AiPoolCodexGemini</span>
                            <span id="settings-source-version" style="display:none">Loading…</span>
                        </div>
                    </div>
                </div>

                <!-- Commit Log Stream Box -->
                <div class="commit-log-box">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; font-size: 10px; color: var(--text-tertiary);">
                        <span>🕒 <span id="settings-commit-date">—</span></span>
                        <span>⏱️ <span id="settings-last-update">Up to date</span></span>
                    </div>
                    <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #cbd5e1;" id="settings-commit-message">Loading latest commit...</div>
                </div>

                <!-- Zero Risk Security Pill -->
                <div style="display: flex; align-items: center; gap: 6px; font-size: 11px; color: #10b981; margin-bottom: 12px; background: rgba(16, 185, 129, 0.08); padding: 8px 10px; border-radius: 6px; border: 1px solid rgba(16, 185, 129, 0.15);">
                    <span>🛡️</span>
                    <span>Zero-Risk: Account tokens, session auth, and port bindings are preserved.</span>
                </div>

                <div id="update-result-box" hidden style="border:1px solid #38bdf8;border-radius:8px;padding:12px;margin-bottom:12px;background:rgba(0,0,0,0.4);">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <strong id="update-result-title"></strong>
                        <span id="update-loading-dots" class="bouncing-dots" style="color: #38bdf8; display: none;">
                            <span></span><span></span><span></span>
                        </span>
                    </div>
                    <pre id="update-result-detail" style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;max-height:260px;overflow:auto;margin-top:6px;"></pre>
                </div>

                <div class="update-action-row">
                    <button class="check-update-btn" id="btn-check-update" onclick="checkRemoteUpdates()">
                        <span id="check-icon">🔍</span>
                        <span id="check-btn-text">Check for Updates</span>
                    </button>
                    <button class="update-cta-btn" id="btn-update-suite" onclick="triggerUpdate()">
                        <span id="update-btn-icon">⚡</span>
                        <span id="update-btn-text">Update from GitHub</span>
                    </button>
                </div>
            </div>

            <!-- Danger Zone (Uninstall) -->
            <div class="glass-card" style="padding: 16px; border: 1px solid rgba(239, 68, 68, 0.3); background: rgba(239, 68, 68, 0.03);">
                <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 12px;">
                    <span style="font-size: 26px;">⚠️</span>
                    <div>
                        <div style="font-size: 15px; font-weight: 700; color: #ef4444;">Danger Zone</div>
                        <div style="font-size: 12px; color: var(--text-secondary);">Uninstall suite, stop/disable all Systemd services, and remove CLI tools.</div>
                    </div>
                </div>
                <div style="background: rgba(0,0,0,0.3); border-radius: 8px; padding: 10px 12px; font-size: 12px; color: #cbd5e1; margin-bottom: 12px;">
                    💡 Note: This will stop all background services and remove commands while preserving account tokens.
                </div>
                <button class="action-btn" onclick="confirmUninstall()" style="width: 100%; background: linear-gradient(135deg, #ef4444, #dc2626); color: #fff; border: none; padding: 12px 14px; border-radius: 8px; font-weight: 700; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px;">
                    <span>🗑️</span>
                    <span>Uninstall Suite & Stop Services</span>
                </button>
            </div>
        </section>
    </div>

    <script>
        let cachedReportData = null;
        let currentProvider = 'Codex';
        let currentSelectedModel = 'gpt-6-astra';
        let clientSessionId = localStorage.getItem('yj_aipool_session') || '';
        const urlParams = new URLSearchParams(window.location.search);
        const urlToken = urlParams.get('token') || '';

        function resolveApiUrl(subpath) {
            const hasAipool = window.location.pathname.startsWith('/aipool');
            const cleanPath = subpath.startsWith('/') ? subpath : '/' + subpath;
            const fullPath = (hasAipool ? '/aipool' : '') + cleanPath;
            const params = new URLSearchParams();
            if (clientSessionId) params.set('session_id', clientSessionId);
            if (urlToken) params.set('token', urlToken);
            const qs = params.toString();
            return fullPath + (qs ? '?' + qs : '');
        }

        function apiHeaders(extraHeaders = {}) {
            const headers = { ...extraHeaders };
            if (clientSessionId) {
                headers['X-Session-ID'] = clientSessionId;
            }
            return headers;
        }

        function toggleDropdown() {
            const wrapper = document.getElementById('custom-model-select');
            wrapper.classList.toggle('open');
        }

        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            const wrapper = document.getElementById('custom-model-select');
            if (wrapper && !wrapper.contains(e.target)) {
                wrapper.classList.remove('open');
            }
        });

        let isSettingsOpen = false;

        function toggleSettingsView() {
            isSettingsOpen = !isSettingsOpen;
            const btn = document.getElementById('settings-toggle-btn');
            const btnIcon = document.getElementById('settings-btn-icon');
            const btnText = document.getElementById('settings-btn-text');
            const providerBar = document.getElementById('provider-segmented-bar');
            const poolSec = document.getElementById('pool-overview-section');
            const streamSec = document.getElementById('stream-section');
            const settingsSec = document.getElementById('settings-view-section');

            if (isSettingsOpen) {
                btn.classList.add('active');
                btnIcon.innerText = '📊';
                btnText.innerText = 'Dashboard';
                providerBar.style.display = 'none';
                poolSec.style.display = 'none';
                streamSec.style.display = 'none';
                settingsSec.style.display = 'flex';
                fetchSettingsStatus();
                fetchCliToolsStatus();
            } else {
                btn.classList.remove('active');
                btnIcon.innerText = '⚙️';
                btnText.innerText = 'Settings';
                providerBar.style.display = '';
                poolSec.style.display = 'block';
                streamSec.style.display = 'block';
                settingsSec.style.display = 'none';
                switchProvider(currentProvider);
            }
        }

        function switchProvider(prov) {
            currentProvider = prov;
            
            const tabCodex = document.getElementById('tab-codex');
            const tabAg = document.getElementById('tab-antigravity');
            
            if (prov === 'Codex') {
                tabCodex.className = 'provider-tab-btn active codex-theme';
                tabAg.className = 'provider-tab-btn antigravity-theme';
                document.getElementById('pool-icon').innerText = '🟣';
                document.getElementById('pool-title-label').innerText = 'ChatGPT Pool';
            } else {
                tabCodex.className = 'provider-tab-btn codex-theme';
                tabAg.className = 'provider-tab-btn active antigravity-theme';
                document.getElementById('pool-icon').innerText = '🟢';
                document.getElementById('pool-title-label').innerText = 'Gemini Pool';
            }

            updateProviderStatsAndModels();
            renderAccountsList();
            if (cachedReportData && cachedReportData.recent_requests) {
                renderGeneralActivityStream(cachedReportData.recent_requests);
            }
        }

        async function fetchSettingsStatus() {
            try {
                const res = await fetch(resolveApiUrl('/api/settings/status'), {
                    headers: apiHeaders(),
                    credentials: 'same-origin'
                });
                if (!res.ok) return;
                const data = await res.json();
                
                const hermesBadge = document.getElementById('hermes-badge');
                if (data.hermes && data.hermes.connected) {
                    hermesBadge.innerText = '🟢 Config verified';
                    hermesBadge.style.background = 'rgba(16, 185, 129, 0.15)';
                    hermesBadge.style.color = '#34d399';
                } else {
                    hermesBadge.innerText = '⚪ Disconnected';
                    hermesBadge.style.background = 'rgba(255, 255, 255, 0.08)';
                    hermesBadge.style.color = '#94a3b8';
                }

                const openclawBadge = document.getElementById('openclaw-badge');
                if (data.openclaw && data.openclaw.connected) {
                    openclawBadge.innerText = '🟢 Config verified';
                    openclawBadge.style.background = 'rgba(16, 185, 129, 0.15)';
                    openclawBadge.style.color = '#34d399';
                } else {
                    openclawBadge.innerText = '⚪ Disconnected';
                    openclawBadge.style.background = 'rgba(255, 255, 255, 0.08)';
                    openclawBadge.style.color = '#94a3b8';
                }
                for (const agent of ['hermes','openclaw']) {
                    const badge = document.getElementById(agent + '-badge');
                    let detail = document.getElementById(agent + '-config-detail');
                    if (!detail) {
                        detail = document.createElement('div');
                        detail.id = agent + '-config-detail';
                        detail.className = 'agent-config-detail-box';
                        badge.closest('.settings-card').appendChild(detail);
                    }
                    detail.textContent = (data[agent]?.config_path || '') + ' — ' + (data[agent]?.message || 'Status unavailable');
                }
            } catch(e) { console.error(e); }
        }

        async function fetchCliToolsStatus() {
            const recheckBtn = document.querySelector('.recheck-btn[onclick="fetchCliToolsStatus()"]');
            if (recheckBtn) {
                recheckBtn.innerHTML = '<span class="spin-icon">🔄</span> Checking <span class="bouncing-dots" style="color: currentColor;"><span></span><span></span><span></span></span>';
                recheckBtn.disabled = true;
            }
            try {
                const res = await fetch(resolveApiUrl('/api/settings/check_cli'), {
                    headers: apiHeaders(),
                    credentials: 'same-origin'
                });
                if (!res.ok) return;
                const data = await res.json();

                // Codex
                const codexBadge = document.getElementById('codex-cli-badge');
                const codexDesc = document.getElementById('codex-cli-desc');
                const codexBtn = document.getElementById('btn-install-codex');
                if (data.codex && data.codex.installed) {
                    codexBadge.innerText = '🟢 Installed';
                    codexBadge.style.background = 'rgba(16, 185, 129, 0.15)';
                    codexBadge.style.color = '#34d399';
                    codexDesc.innerText = `Path: ${data.codex.path || 'Detected in PATH'} ${data.codex.version ? '(' + data.codex.version + ')' : ''}`;
                    codexBtn.style.display = 'none';
                } else {
                    codexBadge.innerText = '🔴 Not Installed';
                    codexBadge.style.background = 'rgba(239, 68, 68, 0.15)';
                    codexBadge.style.color = '#f87171';
                    codexDesc.innerText = "Required for 'c add' interactive login. Click Install to configure.";
                    codexBtn.style.display = 'inline-block';
                }

                // Antigravity
                const agBadge = document.getElementById('antigravity-cli-badge');
                const agDesc = document.getElementById('antigravity-cli-desc');
                const agBtn = document.getElementById('btn-install-antigravity');
                if (data.antigravity && data.antigravity.installed) {
                    agBadge.innerText = '🟢 Installed';
                    agBadge.style.background = 'rgba(16, 185, 129, 0.15)';
                    agBadge.style.color = '#34d399';
                    agDesc.innerText = `Path: ${data.antigravity.path || 'Detected in PATH'}`;
                    agBtn.style.display = 'none';
                } else {
                    agBadge.innerText = '🔴 Not Installed';
                    agBadge.style.background = 'rgba(239, 68, 68, 0.15)';
                    agBadge.style.color = '#f87171';
                    agDesc.innerText = "Required for 'ag add' interactive login. Click Setup to configure.";
                    agBtn.style.display = 'inline-block';
                }
            } catch(e) { console.error(e); }
            finally {
                if (recheckBtn) {
                    recheckBtn.innerHTML = '🔄 Re-check';
                    recheckBtn.disabled = false;
                }
            }
        }

        async function installCliTool(tool) {
            const btn = tool === 'codex' ? document.getElementById('btn-install-codex') : document.getElementById('btn-install-antigravity');
            const originalText = btn.innerHTML;
            btn.innerHTML = '<span><span class="spin-icon">⏳</span></span><span>Installing <span class="bouncing-dots" style="color: currentColor;"><span></span><span></span><span></span></span></span>';
            btn.disabled = true;

            try {
                const res = await fetch(resolveApiUrl('/api/settings/install_cli'), {
                    method: 'POST',
                    headers: apiHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ tool: tool }),
                    credentials: 'same-origin'
                });
                const data = await res.json();
                if (data.success) {
                    btn.innerHTML = '<span>✅</span><span>Done!</span>';
                    fetchCliToolsStatus();
                } else {
                    alert('Installation message: ' + (data.message || 'Failed'));
                    btn.innerHTML = originalText;
                }
            } catch(e) {
                alert('Connection error: ' + e);
                btn.innerHTML = originalText;
            } finally {
                btn.disabled = false;
                setTimeout(() => { fetchCliToolsStatus(); }, 1500);
            }
        }

        async function triggerIntegration(target) {
            const btn = target === 'hermes' ? document.getElementById('btn-sync-hermes') : document.getElementById('btn-sync-openclaw');
            const originalHtml = btn.innerHTML;
            btn.innerHTML = '<span><span class="spin-icon">⚡</span></span><span>Configuring <span class="bouncing-dots" style="color: currentColor;"><span></span><span></span><span></span></span></span>';
            btn.disabled = true;

            try {
                const endpoint = target === 'hermes' ? '/api/settings/integrate_hermes' : '/api/settings/integrate_openclaw';
                const res = await fetch(resolveApiUrl(endpoint), {
                    method: 'POST',
                    headers: apiHeaders(),
                    credentials: 'same-origin'
                });
                const data = await res.json();
                if (data.success) {
                    btn.innerHTML = '<span>✅</span><span>Linked Successfully!</span>';
                    fetchSettingsStatus();
                } else {
                    alert('Integration error: ' + (data.message || 'Failed'));
                    btn.innerHTML = originalHtml;
                }
            } catch(e) {
                alert('Server connection error: ' + e);
                btn.innerHTML = originalHtml;
            } finally {
                btn.disabled = false;
                setTimeout(() => { btn.innerHTML = originalHtml; }, 3000);
            }
        }

        function confirmUninstall() {
            if (confirm('Are you completely sure you want to uninstall the suite and stop all services?\\n\\nThis will stop services immediately and remove CLI shortcuts.')) {
                fetch(resolveApiUrl('/api/settings/uninstall'), {
                    method: 'POST',
                    headers: apiHeaders(),
                    credentials: 'same-origin'
                })
                    .then(res => res.json())
                    .then(data => {
                        alert(data.message || 'Uninstall launched successfully.');
                        window.location.reload();
                    })
                    .catch(e => alert('Error: ' + e));
            }
        }

        function updateProviderStatsAndModels() {
            if (!cachedReportData || !cachedReportData.providers) return;
            const provData = cachedReportData.providers[currentProvider];
            if (!provData) return;

            const totalTokens = provData.total_tokens || 0;
            const reqCount = provData.requests || 0;
            const avg = reqCount > 0 ? Math.round(totalTokens / reqCount) : 0;

            document.getElementById('model-total-tokens').innerText = totalTokens.toLocaleString();
            document.getElementById('model-total-requests').innerText = reqCount.toLocaleString();
            document.getElementById('model-avg-tokens').innerText = avg.toLocaleString();
        }

        function renderAccountsList() {
            if (!cachedReportData || !cachedReportData.status) return;
            const status = cachedReportData.status;
            const container = document.getElementById('pool-accounts-container');
            container.innerHTML = '';

            let accounts = [];
            let poolMetrics = null;

            if (currentProvider === 'Codex') {
                accounts = (status.codex && status.codex.accounts) || [];
                poolMetrics = status.codex && status.codex.pool_metrics;
            } else {
                accounts = (status.antigravity && status.antigravity.accounts) || [];
                poolMetrics = status.antigravity && status.antigravity.pool_metrics;
            }

            // Update Pool Summary Bars
            if (poolMetrics) {
                let h5_pct = 0;
                let wk_pct = 0;
                let tot_accs = poolMetrics.total_accounts || accounts.length;

                if (currentProvider === 'Codex') {
                    h5_pct = poolMetrics['5h_pool_pct'] || 0;
                    wk_pct = poolMetrics['wk_pool_pct'] || 0;
                } else {
                    const gemM = poolMetrics.gemini || {};
                    h5_pct = gemM['5h_pool_pct'] || 0;
                    wk_pct = gemM['wk_pool_pct'] || 0;
                }

                document.getElementById('pool-total-capacity-text').innerText = `${tot_accs} Accounts (${h5_pct}% Active)`;
                document.getElementById('pool-5h-summary-val').innerText = `${h5_pct}%`;
                document.getElementById('pool-5h-progress').style.width = `${Math.min(100, Math.max(0, h5_pct))}%`;
                document.getElementById('pool-wk-summary-val').innerText = `${wk_pct}%`;
                document.getElementById('pool-wk-progress').style.width = `${Math.min(100, Math.max(0, wk_pct))}%`;
            }

            if (accounts.length === 0) {
                container.innerHTML = '<div style="font-size: 11px; color: var(--text-tertiary);">Synchronizing accounts...</div>';
                return;
            }

            accounts.forEach(acc => {
                const isActive = acc.is_active;
                const div = document.createElement('div');
                div.className = 'account-item-pill' + (isActive ? ' active' : '');
                
                let metricsHtml = '';
                if (currentProvider === 'Codex') {
                    const h5 = acc['5h_pct'] !== undefined ? acc['5h_pct'] + '%' : '--';
                    const h5_res = acc['5h_reset'] || '--';
                    const wk = acc['wk_pct'] !== undefined ? acc['wk_pct'] + '%' : '--';
                    const wk_res = acc['wk_reset'] || '--';

                    metricsHtml = `
                        <div style="text-align:right; font-family:'JetBrains Mono',monospace; font-size:10px; line-height:1.4;">
                            <div style="color:var(--accent-cyan); font-weight:700;">5h: ${h5} <span style="color:var(--text-tertiary); font-weight:400;">(${h5_res})</span></div>
                            <div style="color:var(--accent-purple); font-weight:700;">Wk: ${wk} <span style="color:var(--text-tertiary); font-weight:400;">(${wk_res})</span></div>
                        </div>
                    `;
                } else {
                    const gem = acc.gemini || {};
                    const cld = acc.claude || {};
                    const gem_5h = gem['5h_pct'] !== undefined ? gem['5h_pct'] + '%' : '--';
                    const gem_res = gem['5h_reset'] || '--';
                    const wk = gem['wk_pct'] !== undefined ? gem['wk_pct'] + '%' : '--';
                    const wk_res = gem['wk_reset'] || '--';

                    metricsHtml = `
                        <div style="text-align:right; font-family:'JetBrains Mono',monospace; font-size:10px; line-height:1.4;">
                            <div style="color:#67e8f9; font-weight:700;">5h: ${gem_5h} <span style="color:var(--text-tertiary); font-weight:400;">(${gem_res})</span></div>
                            <div style="color:#c084fc; font-weight:700;">Wk: ${wk} <span style="color:var(--text-tertiary); font-weight:400;">(${wk_res})</span></div>
                        </div>
                    `;
                }

                const emailSafe = (acc.email || ('Account ' + acc.account)).replace(/'/g, "\\'");
                div.innerHTML = `
                    <div class="account-normal-content">
                        <div class="acc-info-left" style="display:flex; align-items:center; gap:8px;">
                            <div class="acc-dot ${isActive ? 'active' : ''}"></div>
                            <div style="text-align:left;">
                                <div class="acc-email">${acc.email || ('Account ' + acc.account)}</div>
                                ${isActive ? '<span style="font-size: 9px; background: rgba(16,185,129,0.2); color:#10b981; padding: 2px 7px; border-radius: 6px; font-weight: 800; font-family:monospace; letter-spacing:0.5px;">ACTIVE</span>' : ''}
                            </div>
                        </div>
                        <div class="account-metrics-view">
                            ${metricsHtml}
                        </div>
                    </div>
                    <div class="account-actions-bar">
                        ${isActive 
                            ? `<button type="button" class="account-btn-switch" disabled style="opacity:0.5; cursor:not-allowed;">Active</button>`
                            : `<button type="button" class="account-btn-switch" onclick="event.stopPropagation(); switchAccount(${acc.account}, this)" title="Switch to Account #${acc.account}">⚡ Switch</button>`
                        }
                        ${isActive
                            ? `<button type="button" class="account-btn-delete" disabled style="opacity:0.4; cursor:not-allowed;" title="Cannot delete active account">Active (Protected)</button>`
                            : `<button type="button" class="account-btn-delete" onclick="event.stopPropagation(); openDeleteModal(${acc.account}, '${emailSafe}')" title="Delete Account #${acc.account}">🗑️ Delete</button>`
                        }
                    </div>
                `;

                div.style.cursor = 'pointer';
                div.title = 'Click account to show actions';
                div.onclick = function(e) {
                    if (e.target && e.target.closest && (e.target.closest('.account-btn-switch') || e.target.closest('.account-btn-delete'))) return;
                    const isAlreadySelected = this.classList.contains('selected');
                    document.querySelectorAll('.account-item-pill.selected').forEach(el => {
                        el.classList.remove('selected');
                    });
                    if (!isAlreadySelected) {
                        this.classList.add('selected');
                    }
                };
                container.appendChild(div);
            });
        }

        let pendingDeleteAccount = null;

        function openDeleteModal(accountNum, emailLabel) {
            pendingDeleteAccount = accountNum;
            const labelEl = document.getElementById('modal-del-account-label');
            if (labelEl) labelEl.innerText = `Account #${accountNum} (${emailLabel})`;
            const inputEl = document.getElementById('modal-delete-confirm-input');
            if (inputEl) {
                inputEl.value = '';
            }
            const confirmBtn = document.getElementById('modal-btn-confirm-delete');
            if (confirmBtn) {
                confirmBtn.disabled = true;
                confirmBtn.innerHTML = '🗑️ Delete Account';
            }
            const overlay = document.getElementById('delete-modal-overlay');
            if (overlay) overlay.style.display = 'flex';
            setTimeout(() => { if (inputEl) inputEl.focus(); }, 50);
        }

        function closeDeleteModal(e) {
            if (e && e.target && e.target !== e.currentTarget && !e.target.classList.contains('modal-close-btn') && !e.target.classList.contains('modal-btn-cancel')) return;
            const overlay = document.getElementById('delete-modal-overlay');
            if (overlay) overlay.style.display = 'none';
            pendingDeleteAccount = null;
        }

        function validateDeleteInput() {
            const inputEl = document.getElementById('modal-delete-confirm-input');
            const confirmBtn = document.getElementById('modal-btn-confirm-delete');
            if (!inputEl || !confirmBtn) return;
            confirmBtn.disabled = (inputEl.value.trim().toLowerCase() !== 'confirm');
        }

        async function executeAccountDelete() {
            if (!pendingDeleteAccount) return;
            const inputEl = document.getElementById('modal-delete-confirm-input');
            const confirmation = inputEl ? inputEl.value.trim() : '';
            if (confirmation.toLowerCase() !== 'confirm') return;

            const confirmBtn = document.getElementById('modal-btn-confirm-delete');
            if (confirmBtn) {
                confirmBtn.disabled = true;
                confirmBtn.innerHTML = '<span>⏳</span> Deleting...';
            }

            const system = currentProvider === 'Codex' ? 'codex' : 'antigravity';
            const accNum = pendingDeleteAccount;

            try {
                const res = await fetch(resolveApiUrl('/api/accounts/delete'), {
                    method: 'POST',
                    headers: apiHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ system: system, account: accNum, confirmation: confirmation }),
                    credentials: 'same-origin'
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    closeDeleteModal();
                    showToast(`Account #${accNum} deleted successfully`);

                    // Live DOM update: remove account from cachedReportData immediately without reload
                    const sysKey = system;
                    if (cachedReportData && cachedReportData.status && cachedReportData.status[sysKey] && cachedReportData.status[sysKey].accounts) {
                        cachedReportData.status[sysKey].accounts = cachedReportData.status[sysKey].accounts.filter(a => a.account !== accNum);
                        if (cachedReportData.status[sysKey].pool_metrics) {
                            cachedReportData.status[sysKey].pool_metrics.total_accounts = cachedReportData.status[sysKey].accounts.length;
                        }
                    }
                    renderAccountsList();
                    fetchLiveLogs(false);
                } else {
                    alert('Delete failed: ' + (data.message || 'Unknown error'));
                    if (confirmBtn) {
                        confirmBtn.disabled = false;
                        confirmBtn.innerHTML = '🗑️ Delete Account';
                    }
                }
            } catch (err) {
                alert('Connection error: ' + err);
                if (confirmBtn) {
                    confirmBtn.disabled = false;
                    confirmBtn.innerHTML = '🗑️ Delete Account';
                }
            }
        }

        let accountsVisible = false;

        function toggleAccountsList() {
            const container = document.getElementById('pool-accounts-container');
            const textEl = document.getElementById('accounts-toggle-text');
            const chevronEl = document.getElementById('accounts-toggle-chevron');
            const iconEl = document.getElementById('accounts-toggle-icon');

            accountsVisible = !accountsVisible;
            if (accountsVisible) {
                container.classList.remove('collapsed');
                if (textEl) textEl.innerText = 'Hide Accounts';
                if (iconEl) iconEl.innerText = '🔒';
                if (chevronEl) chevronEl.style.transform = 'rotate(0deg)';
            } else {
                container.classList.add('collapsed');
                if (textEl) textEl.innerText = 'View Accounts';
                if (iconEl) iconEl.innerText = '👁️';
                if (chevronEl) chevronEl.style.transform = 'rotate(180deg)';
            }
        }

        function showToast(msg) {
            const toast = document.getElementById('toast-notification');
            const toastText = document.getElementById('toast-text');
            if (!toast) return;
            if (toastText) toastText.innerText = msg;
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
            }, 2200);
        }

        function escapeHtml(value) {
            const span = document.createElement('span');
            span.textContent = String(value ?? '');
            return span.innerHTML;
        }
        function renderGeneralActivityStream(recentRequests) {
            const listContainer = document.getElementById('activity-stream-list');
            listContainer.innerHTML = '';

            // Combined activity stream showing both Gemini and Codex / ChatGPT in one unified chronological feed
            const requests = recentRequests || [];

            if (requests.length === 0) {
                listContainer.innerHTML = '<div style="text-align:center; padding: 20px; color: var(--text-tertiary);">No activity records yet</div>';
                return;
            }

            requests.forEach(req => {
                const item = document.createElement('div');
                item.className = 'stream-card-item';
                const p = (req.provider || req.pool || '').toLowerCase();
                const m = (req.model || '').toLowerCase();
                const isCodex = p === 'codex' || p === 'chatgpt';
                const badgeClass = isCodex ? 'codex' : 'antigravity';
                const provIcon = isCodex ? '🟣' : '🟢';

                // Ensure pure numeric MM/DD HH:MM format
                let timeStr = req.time_formatted || '--';
                if (req.timestamp) {
                    const d = new Date(req.timestamp * 1000);
                    const mo = String(d.getMonth() + 1).padStart(2, '0');
                    const da = String(d.getDate()).padStart(2, '0');
                    const ho = String(d.getHours()).padStart(2, '0');
                    const mi = String(d.getMinutes()).padStart(2, '0');
                    timeStr = `${mo}/${da} ${ho}:${mi}`;
                }

                item.innerHTML = `
                    <!-- Top Tier: Model on Left | Pure Numeric Date on Right -->
                    <div class="stream-card-top">
                        <div class="stream-model-name">
                            <span>${provIcon}</span>
                            <span style="color:#f8fafc; font-weight:700;">${escapeHtml(req.model)}</span>
                            ${req.call_num ? `<span style="font-size:10px; color:var(--text-tertiary); font-weight:600;">#${escapeHtml(req.call_num)}</span>` : ''}
                        </div>
                        <div style="font-family:'JetBrains Mono',monospace; font-size:11px; color:#94a3b8; font-weight:600;">
                            ${escapeHtml(timeStr)}
                        </div>
                    </div>
                    <!-- Bottom Tier: IN & OUT on Left | Total on Right -->
                    <div style="font-size:10px;color:#94a3b8">${isCodex ? 'Codex' : 'Antigravity'} · ${escapeHtml(req.status || 'Recorded')} ${req.account ? '· Account ' + escapeHtml(req.account) : ''}</div>
                    <div class="stream-card-bottom">
                        <div style="display:flex; align-items:center; gap:8px;">
                            <span style="color:#38bdf8; font-weight:700;">📥 ${req.prompt == null ? "—" : req.prompt.toLocaleString()}</span>
                            <span style="color:rgba(255,255,255,0.15);">|</span>
                            <span style="color:#c084fc; font-weight:700;">📤 ${req.completion == null ? "—" : req.completion.toLocaleString()}</span>
                        </div>
                        <div class="stream-tok-badge ${badgeClass}">
                            <span>⚡</span>
                            <span>+${req.tokens == null ? "unknown" : req.tokens.toLocaleString()} tok</span>
                        </div>
                    </div>
                `;
                listContainer.appendChild(item);
            });
        }

        async function switchAccount(accountNum, btnEl) {
            const system = currentProvider === 'Codex' ? 'codex' : 'antigravity';
            const oldText = btnEl ? btnEl.innerText : 'Switch';
            if (btnEl) {
                btnEl.disabled = true;
                btnEl.innerText = 'Switching...';
            }
            try {
                const res = await fetch(resolveApiUrl('/api/accounts/switch'), {
                    method: 'POST',
                    headers: apiHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ system: system, account: accountNum }),
                    credentials: 'same-origin'
                });
                const data = await res.json();
                if (!res.ok || !data.success) {
                    showToast('⚠️ ' + (data.message || 'Switch failed'));
                    return;
                }
                showToast('✓ ' + (data.message || ('Switched to account #' + accountNum)));
                
                // Live immediate DOM update: mark account active and refresh UI without reload
                const sysKey = system === 'codex' ? 'codex' : 'antigravity';
                if (cachedReportData && cachedReportData.status && cachedReportData.status[sysKey]) {
                    const accList = cachedReportData.status[sysKey].accounts || [];
                    accList.forEach(a => {
                        a.is_active = (Number(a.account) === Number(accountNum));
                    });
                    renderAccountsList();
                }

                await fetchLiveLogs(true);
            } catch (err) {
                console.error('Switch failed:', err);
                showToast('⚠️ Switch request failed');
            } finally {
                if (btnEl) {
                    btnEl.disabled = false;
                    btnEl.innerText = oldText;
                }
            }
        }

        async function fetchLiveLogs(force = false) {
            const btn = document.getElementById('main-refresh-btn') || document.querySelector('.refresh-btn');
            const iconWrap = document.getElementById('main-refresh-icon-wrap');
            const text = document.getElementById('main-refresh-text');
            
            const originalSvg = '<svg class="refresh-svg" id="main-refresh-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>';
            const checkSvg = '<svg class="refresh-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#10b981" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
            const alertSvg = '<svg class="refresh-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#ef4444" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>';

            if (btn) {
                btn.classList.remove('refresh-success');
                btn.classList.add('refreshing');
                if (text && force) text.textContent = 'Syncing…';
            }
            try {
                const path = force ? '/api/refresh' : '/api/logs_data';
                const res = await fetch(resolveApiUrl(path), {
                    headers: apiHeaders(),
                    credentials: 'same-origin'
                });
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                cachedReportData = data;
                
                updateProviderStatsAndModels();
                renderAccountsList();
                renderGeneralActivityStream(data.recent_requests || []);
                
                if (btn && force) {
                    btn.classList.remove('refreshing');
                    btn.classList.add('refresh-success');
                    if (iconWrap) iconWrap.innerHTML = checkSvg;
                    if (text) text.textContent = 'Synced';
                }
            } catch (err) {
                console.error('Failed to sync logs:', err);
                if (btn && force) {
                    btn.classList.remove('refreshing');
                    if (iconWrap) iconWrap.innerHTML = alertSvg;
                    if (text) text.textContent = 'Failed';
                }
            } finally {
                if (btn && force) {
                    setTimeout(() => {
                        btn.classList.remove('refreshing', 'refresh-success');
                        if (iconWrap) iconWrap.innerHTML = originalSvg;
                        if (text) text.textContent = 'Sync Live';
                    }, 2200);
                } else if (btn) {
                    btn.classList.remove('refreshing');
                }
            }
        }

        if (window.__INITIAL_DATA__) {
            cachedReportData = window.__INITIAL_DATA__;
            try {
                switchProvider(currentProvider);
                renderGeneralActivityStream(cachedReportData.recent_requests || []);
            } catch(e) { console.error(e); }
        }

        window.addEventListener('load', () => {
            if (window.__INITIAL_DATA__) {
                cachedReportData = window.__INITIAL_DATA__;
                switchProvider(currentProvider);
                renderGeneralActivityStream(cachedReportData.recent_requests || []);
            }
        });
        setInterval(fetchLiveLogs, 10000);
    </script>
</body>
</html>
"""

GLOBAL_POOL_MANAGER = PoolManager()

def get_settings_status():
    import integrations
    return {agent: integrations.status(agent) for agent in ('hermes','openclaw')}


def integrate_agent(agent):
    import integrations
    try:
        return integrations.integrate(agent)
    except (OSError, ValueError, TypeError) as exc:
        return {'success':False, 'verified':False, 'message':str(exc)}


def get_cli_tools_status():
    from account_manager import find_codex
    codex={'installed':False,'path':'','version':''}
    try:
        binary=find_codex()
        result=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=10)
        codex={'installed':result.returncode==0,'path':binary,'version':result.stdout.strip()}
    except (OSError,ValueError,subprocess.SubprocessError):
        pass
    return {'codex':codex,'antigravity':{'installed':True,'path':'Built-in OAuth','version':'No external CLI required'}}


import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import update_suite
RUNNING_BUILD = update_suite.version()


def get_system_version():
    result = update_suite.version()
    result['running_commit'] = RUNNING_BUILD.get('commit')
    result['running_version'] = RUNNING_BUILD.get('version')
    try:
        result['last_update'] = json.loads(update_suite.result_path().read_text())
    except (OSError, ValueError):
        result['last_update'] = None
    return result

def execute_integration_script(script_name, *extra_args):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_path = os.path.join(base_dir, script_name)
    if not os.path.exists(script_path):
        return False, f"Script not found: {script_path}"
    try:
        cmd = ["bash", script_path] + list(extra_args)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300 if script_name == "update.sh" else 30)
        if res.returncode == 0:
            return True, res.stdout.strip()
        return False, res.stderr.strip() or res.stdout.strip()
    except Exception as e:
        return False, str(e)

class ProDashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Mute normal GET logs to prevent terminal spam
        return

    def send_json_response(self, data, status_code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        client_ip = self.client_address[0]
        # Forwarded requests never qualify for the direct-local maintenance bypass.
        if self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP"):
            client_ip = "proxied"
        if "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()
        user_agent = self.headers.get("User-Agent", "")

        # Session check via Cookie, Header, or Query param
        cookie_header = self.headers.get("Cookie")
        session_cookie = None
        if cookie_header:
            c = cookies.SimpleCookie()
            try:
                c.load(cookie_header)
                if "yj_aipool_session" in c:
                    session_cookie = c["yj_aipool_session"].value
            except Exception: pass

        header_session = self.headers.get("X-Session-ID")
        query_session = qs.get("session_id", [None])[0]
        effective_session = session_cookie or header_session or query_session

        auth_mgr = TokenAuthManager()
        is_auth = False
        new_session_id = None

        # Allow localhost / loopback internally
        if self.client_address[0] in ('127.0.0.1','::1') and not self.headers.get('X-Forwarded-For') and not self.headers.get('X-Real-IP'):
            is_auth = True

        # Check URL query token (e.g. ?token=...)
        if not is_auth and "token" in qs and qs["token"]:
            t = qs["token"][0]
            new_session_id = auth_mgr.register_device(t, client_ip, user_agent)
            if new_session_id:
                is_auth = True
                effective_session = new_session_id

        # Check existing 24h session
        if not is_auth and effective_session:
            if auth_mgr.validate_device(effective_session, client_ip):
                is_auth = True

        # Unauthenticated handling
        if not is_auth:
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_LOGIN_TEMPLATE.encode("utf-8"))
            return

        pm = GLOBAL_POOL_MANAGER

        if path in ('/aipool/assets/update.js', '/assets/update.js'):
            with open(os.path.join(os.path.dirname(__file__), 'update_ui.js'), 'rb') as handle:
                body = handle.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API: logs data
        if path in ("/aipool/api/logs_data", "/api/logs_data"):
            self.send_json_response(pm.get_usage_logs_report())
            return

        # API: force refresh
        if path in ("/aipool/api/refresh", "/api/refresh"):
            pm.trigger_instant_refresh()
            self.send_json_response(pm.get_usage_logs_report())
            return

        # API: pool status data
        if path in ("/aipool/api/status", "/api/status"):
            self.send_json_response(pm.get_all_status())
            return

        # API: settings status
        if path in ("/aipool/api/settings/status", "/api/settings/status"):
            self.send_json_response(get_settings_status())
            return

        # API: system version
        if path in ("/aipool/api/settings/version", "/api/settings/version"):
            self.send_json_response(get_system_version())
            return

        # API: check for updates from remote GitHub
        if path in ("/aipool/api/settings/check_update", "/api/settings/check_update"):
            self.send_json_response(update_suite.check_updates())
            return

        # API: check CLI tools status
        if path in ("/aipool/api/settings/check_cli", "/api/settings/check_cli"):
            self.send_json_response(get_cli_tools_status())
            return

        # Main view - inject initial data server-side so it renders 100% populated immediately with 0ms delay!
        initial_data = pm.get_usage_logs_report()
        initial_json = json.dumps(initial_data, ensure_ascii=False).replace("</script>", "<\\\\/script>")
        sess_val = effective_session or new_session_id or ""
        injected_script = f"""<script>
            window.__INITIAL_DATA__ = {initial_json};
            window.__SESSION_ID__ = "{sess_val}";
            if ("{sess_val}") {{ try {{ localStorage.setItem('yj_aipool_session', "{sess_val}"); }} catch(e){{}} }}
        </script>"""
        html_to_serve = HTML_LOGS_TEMPLATE.replace("</head>", f"{injected_script}\n</head>")
        html_to_serve = html_to_serve.replace("</body>", '<script src="/aipool/assets/update.js"></script></body>')
        body = html_to_serve.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        if new_session_id:
            self.send_header("Set-Cookie", f"yj_aipool_session={new_session_id}; Path=/; Max-Age=86400; HttpOnly; SameSite=Lax; Secure")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get('Host'):
            self.send_json_response({'success': False, 'message': 'Cross-origin writes are not allowed'},403)
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        client_ip = self.client_address[0]
        # Forwarded requests never qualify for the direct-local maintenance bypass.
        if self.headers.get("X-Forwarded-For") or self.headers.get("X-Real-IP"):
            client_ip = "proxied"
        if "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()

        # Check session if auth DB exists
        cookie_header = self.headers.get("Cookie")
        session_cookie = None
        if cookie_header:
            c = cookies.SimpleCookie()
            try:
                c.load(cookie_header)
                if "yj_aipool_session" in c:
                    session_cookie = c["yj_aipool_session"].value
            except Exception:
                pass

        header_session = self.headers.get("X-Session-ID")
        effective_session = session_cookie or header_session

        auth_mgr = TokenAuthManager()
        is_auth = False
        if self.client_address[0] in ('127.0.0.1','::1') and not self.headers.get('X-Forwarded-For') and not self.headers.get('X-Real-IP'):
            is_auth = True
        elif effective_session and auth_mgr.validate_device(effective_session, client_ip):
            is_auth = True

        if not is_auth:
            self.send_json_response({"error": "Unauthorized"}, status_code=401)
            return

        pm = GLOBAL_POOL_MANAGER

        if path in ("/aipool/api/settings/integrate_hermes", "/api/settings/integrate_hermes"):
            self.send_json_response(integrate_agent("hermes"))
            return

        if path in ("/aipool/api/settings/integrate_openclaw", "/api/settings/integrate_openclaw"):
            self.send_json_response(integrate_agent("openclaw"))
            return

        if path in ("/aipool/api/accounts/switch", "/api/accounts/switch"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}
            system = payload.get("system", "")
            try:
                account = int(payload.get("account", 0))
            except (TypeError, ValueError):
                account = 0
            if system not in ("antigravity", "codex") or account < 1:
                self.send_json_response({"success": False, "message": "Invalid system or account number."}, status_code=400)
                return
            ok, detail = pm.switch_account(system, account)
            if not ok:
                self.send_json_response({"success": False, "message": detail or "Switch failed."}, status_code=502)
                return
            self.send_json_response({"success": True, "message": detail, "system": system, "account": account})
            return

        if path in ("/aipool/api/accounts/delete", "/api/accounts/delete"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}
            system = payload.get("system", "")
            try:
                account = int(payload.get("account", 0))
            except (TypeError, ValueError):
                account = 0
            confirmation = payload.get("confirmation", "")
            if system not in ("antigravity", "codex") or account < 1:
                self.send_json_response({"success": False, "message": "Invalid system or account number."}, status_code=400)
                return
            if str(confirmation).strip().lower() != "confirm":
                self.send_json_response({"success": False, "message": "You must type confirm to verify deletion."}, status_code=400)
                return
            ok, detail = pm.delete_account(system, account, confirmation)
            if not ok:
                self.send_json_response({"success": False, "message": detail or "Delete failed."}, status_code=400)
                return
            self.send_json_response({"success": True, "message": detail, "system": system, "account": account})
            return

        if path in ("/aipool/api/settings/update", "/api/settings/update"):
            before = get_system_version()
            ok, message = execute_integration_script('update.sh', '--no-dashboard')
            after = get_system_version()
            response = {'success':ok, 'message':message, 'version_before':before, 'version_after':after,
                        'updated':ok and before.get('commit') != after.get('commit'),
                        'restart_pending':ok}
            try:
                self.send_json_response(response)
            finally:
                if ok:
                    threading.Thread(target=lambda: (time.sleep(2), subprocess.run(['systemctl', '--user', 'restart', 'ai-dashboard.service'])), daemon=True).start()
            return

        if path in ("/aipool/api/settings/install_cli", "/api/settings/install_cli"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}
            tool = payload.get("tool", "")
            if tool not in ("codex", "antigravity"):
                self.send_json_response({"success": False, "message": "Invalid tool specified."}, status_code=400)
                return
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_path = os.path.join(base_dir, "scripts", "install-cli-tools.sh")
            try:
                res = subprocess.run(["bash", script_path, "install", tool], capture_output=True, text=True, timeout=120)
                ok = (res.returncode == 0)
                msg = res.stdout.strip() if ok else (res.stderr.strip() or res.stdout.strip())
                self.send_json_response({"success": ok, "message": msg, "status": get_cli_tools_status()})
            except Exception as e:
                self.send_json_response({"success": False, "message": str(e)}, status_code=500)
            return

        if path in ("/aipool/api/settings/uninstall", "/api/settings/uninstall"):
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            uninstall_script = os.path.join(base_dir, "uninstall.sh")
            if os.path.exists(uninstall_script):
                subprocess.Popen(["bash", "-c", f"sleep 1 && {uninstall_script}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send_json_response({"success": True, "message": "Uninstall process launched in background successfully."})
            else:
                self.send_json_response({"success": False, "message": "uninstall.sh script not found."}, status_code=404)
            return

        self.send_json_response({"error": "Not Found"}, status_code=404)

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def run():
    with ThreadedTCPServer(("127.0.0.1", PORT), ProDashboardHandler) as httpd:
        print(f"Server started on http://127.0.0.1:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()
