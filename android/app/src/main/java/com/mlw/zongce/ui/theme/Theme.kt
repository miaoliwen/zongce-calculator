package com.mlw.zongce.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

// 品牌色与启动图标同源（深绿）
private val LightColors = lightColorScheme(
    primary = Color(0xFF176A52),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFA5F0D2),
    onPrimaryContainer = Color(0xFF002016),
    secondary = Color(0xFF4C635A),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFCEE9DC),
    onSecondaryContainer = Color(0xFF092018),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF89D4B7),
    onPrimary = Color(0xFF003828),
    primaryContainer = Color(0xFF00513C),
    onPrimaryContainer = Color(0xFFA5F0D2),
    secondary = Color(0xFFB3CCBF),
    onSecondary = Color(0xFF1F352C),
    secondaryContainer = Color(0xFF354B42),
    onSecondaryContainer = Color(0xFFCEE9DC),
)

@Composable
fun ZongCeTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = true,
    content: @Composable () -> Unit,
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (darkTheme) dynamicDarkColorScheme(LocalContext.current)
            else dynamicLightColorScheme(LocalContext.current)
        darkTheme -> DarkColors
        else -> LightColors
    }
    MaterialTheme(colorScheme = colorScheme, content = content)
}
