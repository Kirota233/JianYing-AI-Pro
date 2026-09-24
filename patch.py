import sys, re

with open('main.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Add api_model to init
text = text.replace('self.api_key = DEFAULT_KEY\n', 'self.api_key = DEFAULT_KEY\n        self.api_model = "gemini-3.5-flash"\n')

# 2. Add api_model to load_cfg
text = text.replace('self.first_sub = d.get("first_sub", True)\n', 'self.first_sub = d.get("first_sub", True)\n                self.api_model = d.get("api_model", "gemini-3.5-flash")\n')

# 3. Add api_model to save_cfg and update self.api_model
s1 = '''    def _save_cfg(self):
        try:
            with open(CFG_FILE, "w", encoding='utf-8') as f:
                json.dump({"coords": self.coords, "color": self.close_color,
                        "api_key": self.key_entry.get().strip(),
                        "first_run": self.first_run,
                        "first_sub": getattr(self, 'first_sub', True)}, f)
        except: pass'''

s2 = '''    def _save_cfg(self):
        try:
            if hasattr(self, 'model_combo'): self.api_model = self.model_combo.get()
            with open(CFG_FILE, "w", encoding='utf-8') as f:
                json.dump({"coords": self.coords, "color": self.close_color,
                        "api_key": self.key_entry.get().strip(),
                        "first_run": self.first_run,
                        "first_sub": getattr(self, 'first_sub', True),
                        "api_model": getattr(self, 'api_model', "gemini-3.5-flash")}, f)
        except: pass'''
text = text.replace(s1, s2)

# 4. Update Subtitle tab to include sub_log and dual redirection
s3 = '''        for t in ["提取文本为 SRT 格式，并彻底删除原草稿字幕轨道",
                   "统一字体：无差别替换所有草稿字体为 Noto Sans Bold",
                   "解除隐藏：恢复所有被隐藏(闭眼)的字幕轨道显示"]:
            ctk.CTkLabel(info_card, text=f"·  {t}", font=("Segoe UI", 11),
                         text_color=C_MUTED, anchor="w").pack(fill="x", padx=8, pady=2)'''
s4 = s3 + '''
                         
        card_log = self._card(tab)
        card_log.pack(fill="both", expand=True, padx=4, pady=(6, 2))
        self.sub_log = ctk.CTkTextbox(card_log, height=60, corner_radius=6, font=("Consolas", 10),
                                   fg_color=C_LOG_BG, text_color=C_TEXT, border_width=1,
                                   border_color=C_BORDER, state="disabled", wrap="word")
        self.sub_log.pack(fill="both", expand=True, padx=4, pady=4)
        sys.stdout = self._LogWriter(self.log, self.sub_log) if hasattr(self, 'log') else sys.stdout'''
text = text.replace(s3, s4)

# 5. Fix _LogWriter
s5 = '''    class _LogWriter:
        def __init__(self, widget):
            self.w = widget
        def write(self, s):
            self.w.configure(state="normal")
            self.w.insert("end", s)
            self.w.see("end")
            self.w.configure(state="disabled")
        def flush(self): pass'''
s6 = '''    class _LogWriter:
        def __init__(self, *widgets):
            self.ws = widgets
        def write(self, s):
            for w in self.ws:
                if w:
                    w.configure(state="normal")
                    w.insert("end", s)
                    w.see("end")
                    w.configure(state="disabled")
        def flush(self): pass'''
text = text.replace(s5, s6)

# 6. Add error helper before _scan_thread
s7 = '    def _scan_thread(self):'
s8 = '''    def _get_api_err_msg(self, e):
        err = str(e).lower()
        if "403" in err: return "API Key 无效、无权限或未开启代理(403)"
        elif "400" in err: return "请求参数错误或被拒绝(400)"
        elif "429" in err or "quota" in err or "exhausted" in err: return "API 额度已耗尽或请求过于频繁(429)"
        elif "500" in err: return "Gemini 服务器内部错误(500)"
        elif "closed" in err: return "网络连接被强行关闭 (请检查代理是否稳定)"
        elif "timeout" in err: return "网络请求超时 (请检查代理是否可用)"
        return f"发生错误: {str(e)[:50]}"

    def _scan_thread(self):'''
text = text.replace(s7, s8)

# 7. Use error helper in _scan
s9 = '''        except Exception as e:
            self._log(f"✗ {e}"); self.status.configure(text="扫描失败")'''
s10 = '''        except Exception as e:
            msg = self._get_api_err_msg(e)
            self._log(f"✗ {msg}"); self.status.configure(text="扫描失败")
            self.after(0, lambda m=msg: self._show_toast("AI 扫描失败", m, C_DANGER, 6000))'''
text = text.replace(s9, s10)

# 8. Use error helper in _ren_preview
s11 = '''        except Exception as e: 
            err = str(e)
            msg = f"发生错误: {err[:50]}"
            if "403" in err: msg = "API Key 无效、无权限或未开启代理(403)"
            elif "400" in err: msg = "请求参数错误或被拒绝(400)"
            elif "500" in err: msg = "Gemini 服务器内部错误(500)"
            elif "closed" in err: msg = "网络连接被强行关闭 (请检查代理是否稳定)"
            elif "timeout" in err.lower(): msg = "网络请求超时 (请检查代理是否可用)"
            
            self._log(f"✗ {err}")
            self.after(0, lambda m=msg: self._show_toast("AI 重命名失败", m, C_DANGER, 6000))'''
s12 = '''        except Exception as e: 
            msg = self._get_api_err_msg(e)
            self._log(f"✗ {msg}")
            self.after(0, lambda m=msg: self._show_toast("AI 重命名失败", m, C_DANGER, 6000))'''
text = text.replace(s11, s12)

# 9. Add ComboBox to Settings
s13 = '''        self.key_entry = ctk.CTkEntry(card, show="•", corner_radius=6,
                                       border_color=C_BORDER, height=32)
        self.key_entry.insert(0, self.api_key)
        self.key_entry.pack(fill="x", padx=8, pady=(0, 8))
        
        row = ctk.CTkFrame(card, fg_color="transparent")'''
s14 = '''        self.key_entry = ctk.CTkEntry(card, show="•", corner_radius=6,
                                       border_color=C_BORDER, height=32)
        self.key_entry.insert(0, self.api_key)
        self.key_entry.pack(fill="x", padx=8, pady=(0, 8))
        
        row_model = ctk.CTkFrame(card, fg_color="transparent")
        row_model.pack(fill="x", padx=8, pady=(4, 12))
        ctk.CTkLabel(row_model, text="使用的模型:", font=("Segoe UI", 12, "bold"), text_color=C_TEXT).pack(side="left")
        
        models = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3-flash", "gemini-3.7-flash", "gemini-3.8-flash"]
        self.model_combo = ctk.CTkComboBox(row_model, values=models, width=180, corner_radius=6, border_color=C_BORDER, button_color=C_BORDER)
        self.model_combo.set(self.api_model if self.api_model in models else "gemini-3.5-flash")
        self.model_combo.pack(side="right")
        
        row = ctk.CTkFrame(card, fg_color="transparent")'''
text = text.replace(s13, s14)

# 10. Update model in generate_content calls
text = text.replace("model='gemini-3.6-flash'", "model=self.api_model")

# 11. Bump version
text = text.replace('VERSION   = "1.4.3"', 'VERSION   = "1.4.4"')

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(text)

print('Success')
