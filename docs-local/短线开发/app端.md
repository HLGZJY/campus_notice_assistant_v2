# 路线 B：纯 Kotlin 重写完整指南（原生安卓应用）

本指南从零开始，将你现有的 Python 校园通知系统（抓取、提取、待办生成、RAG 问答）用 **纯 Kotlin + Jetpack Compose** 重写为原生 Android 应用。  
最终产物：一个 **≤10MB APK**，数据全在本地（SQLite + 本地向量），支持定时后台抓取，界面流畅如原生，可长期自用或分享。

---

## 1. 技术选型总览（全部纯 Kotlin/JVM 库）

| 功能模块             | 选型                                | 说明                                           |
| :------------------- | :---------------------------------- | :--------------------------------------------- |
| **网络请求**         | `ktor-client`（OkHttp 引擎）        | 轻量、协程支持、可取消                         |
| **HTML 解析**        | `ksoup`（Kotlin 移植的 Jsoup）      | 纯 Kotlin，无 Java 依赖，体积小                |
| **数据库**           | `Room`（SQLite 官方 ORM）           | 类型安全，支持 Flow 观察数据变化               |
| **偏好设置**         | `DataStore`（Proto 或 Preferences） | 存储 API Key 和用户配置                        |
| **向量检索**         | 纯 Kotlin `FloatArray` 点积         | 无需任何第三方库，暴力检索 <10ms（200 条向量） |
| **后台任务**         | `WorkManager`                       | 适配 Doze 模式，定时抓取通知                   |
| **UI 框架**          | `Jetpack Compose` + Material 3      | 声明式、原生体验                               |
| **异步编程**         | `Kotlin Coroutines` + `Flow`        | 非阻塞、生命周期感知                           |
| **依赖注入**（可选） | `Koin` 或手动构造函数注入           | 便于测试，本指南采用手动注入保持简单           |

---

## 2. 项目初始化（Android Studio）

1. 打开 Android Studio（Ladybug 或更高版本）。
2. 新建项目 → **Empty Activity**（或 **Empty Compose Activity**）。
   - 项目名称：`CampusHelper`（示例）
   - 包名：`com.yourname.campushelper`
   - 最低 SDK：**Android 8.0（API 26）**（覆盖绝大多数设备）
   - 语言：**Kotlin**
3. 等待 Gradle 同步完成。

### 添加依赖（`app/build.gradle.kts`）

```kotlin
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("kotlin-kapt")   // 用于 Room 编译时处理
    id("kotlin-parcelize") // 可选的 Parcelable 支持
}

android {
    namespace = "com.yourname.campushelper"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.yourname.campushelper"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildFeatures {
        compose = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.4"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // Compose 基础
    implementation(platform("androidx.compose:compose-bom:2024.02.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.8.0")

    // 协程 + 生命周期
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")

    // Ktor Client (纯 Kotlin)
    implementation("io.ktor:ktor-client-core:2.3.7")
    implementation("io.ktor:ktor-client-okhttp:2.3.7")
    implementation("io.ktor:ktor-client-content-negotiation:2.3.7")
    implementation("io.ktor:ktor-serialization-kotlinx-json:2.3.7")

    // HTML 解析 (ksoup)
    implementation("com.fleeksoft.ksoup:ksoup:0.1.2")

    // Room 数据库
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    kapt("androidx.room:room-compiler:2.6.1")

    // DataStore
    implementation("androidx.datastore:datastore-preferences:1.1.0")

    // WorkManager
    implementation("androidx.work:work-runtime-ktx:2.9.0")

    // 图片加载（可选）
    implementation("io.coil-kt:coil-compose:2.6.0")

    // 权限处理（可选）
    implementation("com.google.accompanist:accompanist-permissions:0.32.0")
}
```

同步后，可开始编写代码。

---

## 3. 数据模型与 Room 数据库

### 3.1 实体类（`data/entity/`）

```kotlin
// Notice.kt
import androidx.room.Entity
import androidx.room.PrimaryKey
import java.util.Date

@Entity(tableName = "notices")
data class Notice(
    @PrimaryKey val url: String,
    val title: String,
    val publishDate: Date?,
    val deadline: Date?,
    val abstract: String?,
    val rawContent: String?,
    val source: String,
    var status: String = "raw",  // raw, extracted, indexed
    var contentHash: String = "",
    var fetchedAt: Date = Date()
)

// Todo.kt
@Entity(tableName = "todos")
data class Todo(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val noticeUrl: String,
    val content: String,
    val deadline: Date?,
    val isDone: Boolean = false,
    val createdAt: Date = Date()
)

// CrawlLog.kt
@Entity(tableName = "crawl_logs")
data class CrawlLog(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val source: String,
    val startTime: Date,
    val endTime: Date?,
    val count: Int = 0,
    val success: Boolean = false,
    val message: String? = null
)

// TokenUsage.kt（计量）
@Entity(tableName = "token_usage")
data class TokenUsage(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val taskType: String,  // extraction, todo, qa, embedding
    val model: String,
    val inputTokens: Int,
    val outputTokens: Int,
    val success: Boolean = true,
    val retryCount: Int = 0,
    val noticeId: String? = null,
    val createdAt: Date = Date()
)

// Subscription.kt（订阅）
@Entity(tableName = "subscriptions")
data class Subscription(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val keyword: String,
    val type: String? = null,  // optional
    val enabled: Boolean = true
)

// Reminder.kt（提醒）
@Entity(tableName = "reminders")
data class Reminder(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val noticeUrl: String,
    val todoId: Long? = null,
    val deadline: Date,
    val triggerDay: Int,  // 3 or 1 (days before)
    val status: String = "pending", // pending, read, ignored
    val createdAt: Date = Date()
)

// Event.kt（埋点）
@Entity(tableName = "events")
data class UserEvent(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val eventType: String,
    val targetId: String? = null,
    val extra: String? = null,
    val timestamp: Date = Date()
)
```

### 3.2 数据库实例（`data/database/AppDatabase.kt`）

```kotlin
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import android.content.Context

@Database(
    entities = [Notice::class, Todo::class, CrawlLog::class, TokenUsage::class,
                Subscription::class, Reminder::class, UserEvent::class],
    version = 1,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun noticeDao(): NoticeDao
    abstract fun todoDao(): TodoDao
    abstract fun crawlLogDao(): CrawlLogDao
    abstract fun tokenUsageDao(): TokenUsageDao
    abstract fun subscriptionDao(): SubscriptionDao
    abstract fun reminderDao(): ReminderDao
    abstract fun eventDao(): EventDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getInstance(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "campus_helper.db"
                ).build()
                INSTANCE = instance
                instance
            }
        }
    }
}
```

Dao 接口省略（增删改查方法），可根据 Python 代码逻辑写出对应查询（例如 `getNoticesByStatus`, `insertTodo`, `deleteTodo` 等）。

---

## 4. 网络层：Ktor + ksoup 爬虫

### 4.1 搭建 Ktor 客户端（`network/HttpClient.kt`）

```kotlin
import io.ktor.client.HttpClient
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json

val httpClient = HttpClient(OkHttp) {
    install(ContentNegotiation) {
        json(Json {
            ignoreUnknownKeys = true
            isLenient = true
        })
    }
    followRedirects = true
    expectSuccess = false
}
```

### 4.2 爬虫逻辑（`network/Crawler.kt`）

```kotlin
import com.fleeksoft.ksoup.Ksoup
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import io.ktor.client.request.get
import io.ktor.client.statement.bodyAsText

class Crawler {
    suspend fun fetchList(source: String, listUrl: String): List<NoticePreview> {
        val html = httpClient.get(listUrl).bodyAsText()
        val doc = Ksoup.parse(html)
        // 根据实际校园网 HTML 结构选择选择器
        val items = doc.select("ul.list li a") // 示例
        return items.mapNotNull { elem ->
            val title = elem.text()
            val link = elem.attr("href").let { if (it.startsWith("http")) it else "$baseUrl$it" }
            NoticePreview(title, link)
        }
    }

    suspend fun fetchDetail(url: String): NoticeDetail? {
        val html = httpClient.get(url).bodyAsText()
        val doc = Ksoup.parse(html)
        // 提取标题、发布时间、正文、截止日期（假设有特定class）
        val title = doc.select("h1.title").text()
        val publishDate = doc.select("span.publish-date").text()?.let { parseDate(it) }
        val content = doc.select("div.content").text()
        // 提取 deadline 若存在
        return NoticeDetail(title, publishDate, content, null) // 暂缺 deadline
    }
}
```

### 4.3 数据类定义

```kotlin
data class NoticePreview(val title: String, val url: String)
data class NoticeDetail(val title: String, val publishDate: Date?, val content: String, val deadline: Date?)
```

### 4.4 爬虫服务封装（`service/CrawlService.kt`）

```kotlin
class CrawlService(private val dao: NoticeDao) {
    suspend fun crawlSource(sourceConfig: SourceConfig) {
        // 抓取列表，对比已有 URL，只取新 URL
        // 抓取详情，插入或更新（根据 contentHash）
        // 记录 crawl_log
    }
}
```

---

## 5. LLM 调用（DashScope API）

### 5.1 API 客户端（`network/DashscopeClient.kt`）

```kotlin
import io.ktor.client.request.*
import io.ktor.http.*

class DashscopeClient(private val apiKey: String) {
    private val baseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    suspend fun chatCompletion(
        model: String = "qwen-plus",
        messages: List<Message>,
        temperature: Double = 0.1,
        maxTokens: Int = 2048
    ): ChatResponse {
        val response = httpClient.post("$baseUrl/chat/completions") {
            header("Authorization", "Bearer $apiKey")
            contentType(ContentType.Application.Json)
            setBody(
                mapOf(
                    "model" to model,
                    "messages" to messages,
                    "temperature" to temperature,
                    "max_tokens" to maxTokens
                )
            )
        }
        return response.body()
    }

    suspend fun getEmbedding(text: String): FloatArray {
        // 调用 /embeddings 端点
        // 返回 float 数组
    }
}

data class Message(val role: String, val content: String)
data class ChatResponse(val choices: List<Choice>, val usage: Usage)
data class Choice(val message: Message)
data class Usage(val prompt_tokens: Int, val completion_tokens: Int)
```

### 5.2 提取器（`core/Extractor.kt`）

```kotlin
class Extractor(private val client: DashscopeClient) {
    suspend fun extractNotice(rawContent: String, title: String): ExtractedInfo {
        val prompt = """
            你是一个信息提取助手。从以下通知中提取：
            1. 摘要（50字内）
            2. 截止日期（如果存在，格式 yyyy-MM-dd）
            3. 关键动作（待办事项）
            通知内容：${rawContent}
            返回 JSON 格式：{"summary": "...", "deadline": "...", "todos": ["..."]}
        """
        val response = client.chatCompletion(
            messages = listOf(Message("user", prompt))
        )
        // 解析 JSON 返回 ExtractedInfo
        return parseExtractedInfo(response.choices[0].message.content)
    }
}
```

### 5.3 待办生成器（`core/TodoGenerator.kt`）

类似地调用 API，从提取的 todos 生成待办条目，存入数据库。

### 5.4 问答 RAG（`core/QA.kt`）

```kotlin
class QA(
    private val vectorStore: VectorStore,
    private val client: DashscopeClient
) {
    suspend fun answer(question: String): Pair<String, List<String>> {
        // 1. 获取问题嵌入
        val qEmb = client.getEmbedding(question)
        // 2. 检索 top5
        val chunks = vectorStore.search(qEmb, topK = 5)
        // 3. 构建 prompt
        val context = chunks.joinToString("\n") { it.text }
        val prompt = """
            根据以下文档片段回答问题。如果无法回答，请说“未找到相关信息”。
            文档：${context}
            问题：${question}
            回答：
        """
        val response = client.chatCompletion(messages = listOf(Message("user", prompt)))
        return Pair(response.choices[0].message.content, chunks.map { it.source })
    }
}
```

---

## 6. 本地向量存储（纯 Kotlin）

### 6.1 向量结构

```kotlin
data class Chunk(
    val id: String,
    val noticeUrl: String,
    val text: String,
    val embedding: FloatArray
)
```

### 6.2 向量存储类（`storage/VectorStore.kt`）

```kotlin
import kotlin.math.sqrt

class VectorStore(private val db: AppDatabase) {
    // 从数据库加载所有向量（可缓存）
    suspend fun search(query: FloatArray, topK: Int): List<Chunk> {
        val all = db.chunkDao().getAll() // 需要建 Chunk 表，或者从 Notice 动态分块
        return all.map { chunk ->
            val sim = cosineSimilarity(query, chunk.embedding)
            chunk to sim
        }.sortedByDescending { it.second }
            .take(topK)
            .map { it.first }
    }

    private fun cosineSimilarity(a: FloatArray, b: FloatArray): Float {
        var dot = 0f
        var normA = 0f
        var normB = 0f
        for (i in a.indices) {
            dot += a[i] * b[i]
            normA += a[i] * a[i]
            normB += b[i] * b[i]
        }
        return dot / (sqrt(normA) * sqrt(normB) + 1e-9f)
    }
}
```

**注意**：你需要将通知内容切分成块（例如按句号分割，每 256 token 一块），并存储嵌入到 Room 的 `Chunk` 表中。嵌入维度取决于你所用的模型（例如 dashscope 的 `text-embedding-v1` 输出 1536 维）。

---

## 7. 业务服务层（协调各模块）

```kotlin
class NoticeService(
    private val noticeDao: NoticeDao,
    private val crawler: Crawler,
    private val extractor: Extractor,
    private val todoGenerator: TodoGenerator,
    private val vectorStore: VectorStore
) {
    suspend fun refreshAll() {
        // 1. 爬取所有源
        // 2. 对未提取的通知执行 extractor
        // 3. 生成待办并存入
        // 4. 更新向量索引
    }
}
```

---

## 8. UI 层（Jetpack Compose + ViewModel）

### 8.1 整体结构

- `MainActivity.kt`：设置导航（底部标签栏）。
- 四个页面：
  - **通知列表** (`NoticeListScreen`)
  - **待办清单** (`TodoScreen`)
  - **问答** (`QAScreen`)
  - **设置** (`SettingsScreen`)

### 8.2 ViewModel 示例（`ui/qa/QAViewModel.kt`）

```kotlin
class QAViewModel(
    private val qa: QA,
    private val tokenUsageDao: TokenUsageDao
) : ViewModel() {
    private val _answer = MutableStateFlow("")
    val answer: StateFlow<String> = _answer
    private val _sources = MutableStateFlow<List<String>>(emptyList())
    val sources: StateFlow<List<String>> = _sources
    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading

    fun ask(question: String) {
        viewModelScope.launch {
            _isLoading.value = true
            try {
                val (ans, srcs) = qa.answer(question)
                _answer.value = ans
                _sources.value = srcs
            } catch (e: Exception) {
                _answer.value = "出错：${e.message}"
            } finally {
                _isLoading.value = false
            }
        }
    }
}
```

### 8.3 Compose 页面片段（`ui/qa/QAScreen.kt`）

```kotlin
@Composable
fun QAScreen(viewModel: QAViewModel = viewModel()) {
    var question by remember { mutableStateOf("") }
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        OutlinedTextField(
            value = question,
            onValueChange = { question = it },
            label = { Text("输入问题") },
            modifier = Modifier.fillMaxWidth()
        )
        Button(
            onClick = { viewModel.ask(question) },
            enabled = !viewModel.isLoading.value
        ) {
            Text(if (viewModel.isLoading.value) "思考中..." else "提问")
        }
        Spacer(modifier = Modifier.height(16.dp))
        Text(text = viewModel.answer.value)
        viewModel.sources.value.forEach {
            Text(text = "来源：$it", fontSize = 12.sp, color = Color.Gray)
        }
    }
}
```

其他页面类似，调用相应的 Service 方法，并用 `LazyColumn` 展示列表。

---

## 9. 后台定时抓取（WorkManager）

### 9.1 创建 Worker（`worker/CrawlWorker.kt`）

```kotlin
import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class CrawlWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        return withContext(Dispatchers.IO) {
            try {
                val db = AppDatabase.getInstance(applicationContext)
                val noticeDao = db.noticeDao()
                // 注入依赖... 或者从全局单例获取
                val service = NoticeService(/* 依赖注入 */)
                service.refreshAll()
                Result.success()
            } catch (e: Exception) {
                e.printStackTrace()
                Result.retry()
            }
        }
    }
}
```

### 9.2 调度 WorkManager（在 `Application` 或首屏调用）

```kotlin
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

fun scheduleCrawl(context: Context) {
    val workRequest = PeriodicWorkRequestBuilder<CrawlWorker>(
        repeatInterval = 60, TimeUnit.MINUTES // 可改为 6 小时
    ).build()
    WorkManager.getInstance(context).enqueueUniquePeriodicWork(
        "crawl_work",
        ExistingPeriodicWorkPolicy.KEEP,
        workRequest
    )
}
```

---

## 10. 配置存储（DataStore）

```kotlin
// 在 SettingsScreen 中读写 API Key
val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

suspend fun saveApiKey(context: Context, key: String) {
    context.settingsDataStore.edit { it[PreferencesKeys.API_KEY] = key }
}

suspend fun getApiKey(context: Context): String {
    return context.settingsDataStore.data.map { it[PreferencesKeys.API_KEY] ?: "" }.first()
}
```

配置页 UI 提供输入框，保存后刷新全局 `DashscopeClient` 实例。

---

## 11. 权限处理

在 `AndroidManifest.xml` 添加：

```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
```

如果需要后台下载时保持网络，添加 `FOREGROUND_SERVICE`（非必须）。

---

## 12. 打包 APK

- 在 Android Studio 菜单：**Build → Build Bundle(s) / APK(s) → Build APK**。
- 或者命令行：`./gradlew assembleDebug`（调试版）或 `assembleRelease`（需签名）。

生成的 APK 在 `app/build/outputs/apk/debug/` 下。

**签名发布**（给他人安装）：Android Studio → Build → Generate Signed Bundle / APK。

---

## 13. 扩展性说明（本路线优势）

- **添加新通知源**：只需在 `SourceConfig` 增加一条，爬虫逻辑通用。
- **切换/添加 LLM 模型**：在 `DashscopeClient` 里增加 `model` 参数，或在设置里让用户选择。
- **增加推送通知**：利用 `WorkManager` 完成抓取后判断订阅，触发 `NotificationManager`。
- **多用户**：数据全在本地，若需同步可加 `Firebase` 或自建后端，不影响 UI。
- **迁移到 iOS**：使用 Kotlin Multiplatform 共享业务逻辑，UI 层用 SwiftUI。

---

## 14. 调试建议

- 使用 `Log.d("CampusHelper", "message")` 查看输出。
- 网络请求失败时开启 Ktor 的日志插件（`Logging`）。
- 利用 Android Studio 的 Database Inspector 查看 SQLite 数据。

---

## 15. 最终检查清单

- [ ] 所有依赖同步成功
- [ ] Room 数据库版本 1，实体正确
- [ ] 爬虫可正常解析目标网站（可能需要适配实际 CSS 选择器）
- [ ] API 调用返回正确结构，错误处理完善
- [ ] 向量检索返回相关结果
- [ ] WorkManager 触发后能后台运行（需允许后台网络）
- [ ] UI 适配暗色模式（Material 3 自动支持）

完成以上，你就拥有了一个原生 Android 的校园助手应用，不依赖任何 Python 环境，轻量、快速、可拓展。祝你编码顺利！
