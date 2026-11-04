#include "llama.h"
#include "ggml-backend.h"
#include <chrono>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

// CPU-only model loading. No context, tokenization, warmup or evaluation.
int main(int argc, char ** argv) {
    if (argc != 4) return 2;
    const std::string mode = argv[3];
    if (mode != "auto" && mode != "none" && mode != "mmap" && mode != "mlock" &&
        mode != "mmap+mlock" && mode != "dio") return 2;
    ggml_backend_load_all_from_path(argv[2]);
    llama_backend_init();
    auto params = llama_model_default_params();
    params.n_gpu_layers = 0;
    params.load_mode = llama_load_mode_from_str(argv[3]);
    const auto start = std::chrono::steady_clock::now();
    llama_model * model = llama_model_load_from_file(argv[1], params);
    const auto end = std::chrono::steady_clock::now();
    if (!model) { llama_backend_free(); return 1; }
    const double seconds = std::chrono::duration<double>(end - start).count();
    std::cout << "{\"model_load_s\":" << std::setprecision(12) << seconds << "}\n";
    llama_model_free(model);
    llama_backend_free();
    return 0;
}
