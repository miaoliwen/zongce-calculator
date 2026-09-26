// Windows 下仓库路径含中文（综测计算器），Gradle 测试 worker 的 classpath
// argfile 会因编码问题乱码导致 java.lang.ClassNotFoundException。
// 把构建目录统一指到 ASCII 路径规避（源码位置不变）。
@Suppress("DEPRECATION")
val asciiBuildRoot = java.io.File(
    System.getenv("LOCALAPPDATA") ?: System.getProperty("java.io.tmpdir"),
    "zongce-android-build",
)

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}

@Suppress("DEPRECATION")
allprojects {
    buildDir = asciiBuildRoot.resolve(name)
}
