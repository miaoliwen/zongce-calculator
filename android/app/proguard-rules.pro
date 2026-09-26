# kotlinx.serialization：保留序列化器与 @Serializable 类的泛型签名
-keepattributes *Annotation*, InnerClasses, Signature
-dontnote kotlinx.serialization.**
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.json.** { kotlinx.serialization.KSerializer serializer(...); }
-keep,includedescriptorclasses class com.mlw.zongce.**$$serializer { *; }
-keepclassmembers class com.mlw.zongce.** { *** Companion; }
-keepclasseswithmembers class com.mlw.zongce.** { kotlinx.serialization.KSerializer serializer(...); }

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**
