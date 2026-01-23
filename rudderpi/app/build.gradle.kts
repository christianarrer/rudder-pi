plugins {
    alias(libs.plugins.android.application)
}

android {
    namespace = "biz.schrottplatz.rudderpi"
    compileSdk {
        version = release(36)
    }

    defaultConfig {
        applicationId = "biz.schrottplatz.rudderpi"
        minSdk = 34
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    //implementation("com.github.pedroSG94:RTSP-Server:1.3.6")
    implementation("com.github.pedroSG94.rtmp-rtsp-stream-client-java:rtplibrary:2.2.2")
    //implementation("com.google.android.exoplayer:exoplayer-ui:2.19.1")
    //implementation("com.github.pedroSG94.RootEncoder:library:2.6.7")
    //implementation("com.github.pedroSG94.RootEncoder:extra-sources:2.6.7")
    implementation(libs.nanohttpd.webserver)
    implementation(libs.appcompat)
    implementation(libs.material)
    implementation(libs.constraintlayout)
    implementation(libs.navigation.fragment)
    implementation(libs.navigation.ui)
    testImplementation(libs.junit)
    androidTestImplementation(libs.ext.junit)
    androidTestImplementation(libs.espresso.core)
}
