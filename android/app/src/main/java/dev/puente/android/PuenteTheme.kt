package dev.puente.android

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

@Composable
fun PuenteTheme(content: @Composable () -> Unit) {
    val colors = if (isSystemInDarkTheme()) darkColorScheme(
        primary = Color(0xFFF2F2F3), onPrimary = Color(0xFF161618),
        background = Color(0xFF0A0A0B), onBackground = Color(0xFFF2F2F3),
        surface = Color(0xFF0A0A0B), onSurface = Color(0xFFF2F2F3),
        surfaceContainer = Color(0xFF19191C), surfaceContainerLow = Color(0xFF161618),
        secondaryContainer = Color(0xFF303034), onSecondaryContainer = Color(0xFFF2F2F3),
        onSurfaceVariant = Color(0xFFAAAAB2), outlineVariant = Color(0xFF303034),
    ) else lightColorScheme(
        primary = Color(0xFF20252C), onPrimary = Color.White,
        background = Color(0xFFF8F9FB), onBackground = Color(0xFF16191D),
        surface = Color(0xFFF8F9FB), onSurface = Color(0xFF16191D),
        surfaceContainer = Color.White, surfaceContainerLow = Color.White,
        secondaryContainer = Color(0xFFE1E7EF), onSecondaryContainer = Color(0xFF20252C),
        onSurfaceVariant = Color(0xFF58616B), outlineVariant = Color(0xFFDDE1E6),
    )
    MaterialTheme(colorScheme = colors,
        shapes = Shapes(extraSmall = RoundedCornerShape(4.dp), small = RoundedCornerShape(6.dp),
            medium = RoundedCornerShape(8.dp), large = RoundedCornerShape(12.dp), extraLarge = RoundedCornerShape(16.dp)),
        content = content)
}
