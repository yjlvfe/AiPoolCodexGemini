# AGENTS.md - دليل وقواعد ذكاء منظومة AiPool (Codex & Antigravity)

هذا الملف مخصص لتعليمات وتوجيهات نماذج الذكاء الاصطناعي (AI Agents) عند العمل على صيانة أو تطوير مشروع **AiPoolCodexGemini**.

---

## 🚨 القاعدة الذهبية: التطابق الكامل بين التيرمنال والواجهة (UI & CLI Parity)
- **أي تعديل أو ميزة تمس بيانات الحسابات، الأرصدة، أو الحالات يجب أن يُطبق في الجهتين معاً في نفس الوقت:**
  1. **جهة التيرمنال (CLI):** ملفات `cli/usage_format.py` و `cli/account_manager.py` وأوامر `cusage` و `agusage`.
  2. **جهة لوحة التحكم (Dashboard):** ملفات `cli/account_reports.py` و `dashboard/app.py` و `dashboard/templates/dashboard.html`.
- يُمنع منعاً باتاً تعديل منطق الحسابات في جهة وإهمال الجهة الأخرى لتفادي كسر التزامن بين ما يراه المستخدم في التيرمنال وما يراه في المتصفح.
- التعديل يُعتبر مكتملاً فقط إذا أعطى التيرمنال والموقع نفس النتيجة لنفس الحالة.

---

## 🏛️ معمارية المشروع والمنافذ والمسارات

### 1. المسارات الأساسية:
- **المستودع الرئيسي (Source Repo):** `/root/Projects/AiPoolCodexGemini`
- **بيئة التشغيل المعزولة (Production Non-Root):** `/var/lib/aipool/app`
- **مجلد تخزين الحسابات والبيانات:** `/var/lib/aipool/accounts/` (مقسم إلى `codex/` و `antigravity/`)
- **قاعدة بيانات الجلسات والتوثيق:** `/var/lib/aipool/runtime/auth.db` و `/var/lib/aipool/runtime/pool_snapshot.json`

### 2. خدمات النظام (System Services):
- `aipool-codex-bridge.service`: جسر OpenAI/Codex على المنفذ **8124** (المستخدم: `aipool`).
- `aipool-gemini-bridge.service`: جسر Google Antigravity على المنفذ **8123** (المستخدم: `aipool`).
- `aipool-dashboard.service`: سيرفر لوحة التحكم Web UI على المنفذ **8444** (المستخدم: `aipool`).

---

## 🔒 قواعد الأقفال وإدارة العمليات (Lock & Anti-Hang Contract)
1. **حظر الأقفال اللانهائية (No Infinite Blocking):**
   - استخدام `fcntl.flock` يكون دائماً بمؤقت محدد (مثل `timeout=5.0s`) أو بنمط `LOCK_NB`.
   - استخدام علم `O_CLOEXEC` عند فتح أي ملف قفل لمنع توريث واصفات الملفات للعمليات الفرعية (`subprocess`).
2. **صلاحيات الملفات ومستخدم التشغيل:**
   - تعمل الخدمات تحت المستخدم المعزول `aipool`. أي إنشاء أو تعديل لحسابات أو ملفات يجب أن يضمن صلاحيات `aipool:aipool`.

---

## 👥 قواعد الحسابات (Plus vs Free) والتدوير التلقائي
1. **حسابات Plus:**
   - مراقبة وعرض نافذة الـ **5 ساعات (5-hour)** ونافذة الـ **أسبوع (Weekly)** مع نسب الاستهلاك ووقت التصفير.
2. **حسابات Free:**
   - قراءة وعرض نافذة الاستهلاك **الشهرية (Monthly - 43200m)** فقط في التيرمنال واللوحة.
3. **الحسابات المسجلة خروج (SIGNED OUT):**
   - عند اكتشاف إلغاء الجلسة (`refresh_token_invalidated`)، لا يجوز تعليق النظام، ويتم إتاحة أمر `relogin` المباشر لإعادة ربط الحساب فوراً.
4. **الترتيب التلقائي وضغط الخانات (Auto-Compacting):**
   - عند حذف أي خانة (مثل C1)، يتم ضغط وإعادة ترقيم الخانات التالية فوراً ليظل المجمع مستمراً (`1..N`) بدون فجوات رقمية.

---

## 🚀 دورة النشر والتحديث (Deployment Contract)
- عند تعديل أي ملف كود في `/root/Projects/AiPoolCodexGemini`:
  1. نفّذ أمر الترحيل إلى بيئة التشغيل:
     ```bash
     python3 /root/Projects/AiPoolCodexGemini/scripts/aipool_nonroot_migration.py --apply
     ```
  2. تأكد من ضبط الصلاحيات لمستخدم الخدمة:
     ```bash
     chown -R aipool:aipool /var/lib/aipool/accounts /var/lib/aipool/runtime
     ```
  3. أعد تشغيل الخدمات المعنية (`systemctl restart aipool-*.service`).
  4. تحقق من مطابقة مخرجات التيرمنال مع اللوحة قبل إنهاء المهمة.
