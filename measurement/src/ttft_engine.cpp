#include "llama.h"
#include "ggml-backend.h"
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>

// One model/context per process. No warmup or second generated token.
int main(int argc, char ** argv) {
    if (argc != 5) return 2;
    try {
        const int threads = std::stoi(argv[3]);
        const int count = std::stoi(argv[4]);
        if (threads < 1 || count < 1 || count > 1048576) return 2;
        ggml_backend_load_all_from_path(argv[2]);
        llama_backend_init();
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = 0;
        std::unique_ptr<llama_model, decltype(&llama_model_free)> model(
            llama_model_load_from_file(argv[1], mp), llama_model_free);
        if (!model) throw std::runtime_error("model load failed");
        const auto * vocab = llama_model_get_vocab(model.get());
        const int n_vocab = llama_vocab_n_tokens(vocab);
        if (n_vocab < 1) throw std::runtime_error("empty vocabulary");
        // Same synthetic-token policy as llama-bench, fixed seed per process.
        std::srand(1);
        std::vector<llama_token> tokens(count);
        for (auto & token : tokens) token = std::rand() % n_vocab;
        if (llama_vocab_get_add_bos(vocab)) tokens[0] = llama_vocab_bos(vocab);
        auto cp = llama_context_default_params();
        cp.n_ctx = count + 32;
        cp.n_batch = count;
        cp.n_ubatch = 512;
        cp.n_threads = threads;
        cp.n_threads_batch = threads;
        cp.no_perf = false;
        cp.offload_kqv = false;
        cp.op_offload = false;
        std::unique_ptr<llama_context, decltype(&llama_free)> ctx(
            llama_init_from_model(model.get(), cp), llama_free);
        if (!ctx) throw std::runtime_error("context creation failed");
        auto sp = llama_sampler_chain_default_params();
        sp.no_perf = false;
        std::unique_ptr<llama_sampler, decltype(&llama_sampler_free)> sampler(
            llama_sampler_chain_init(sp), llama_sampler_free);
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_greedy());
        llama_perf_context_reset(ctx.get());
        llama_perf_sampler_reset(sampler.get());
        const int64_t started = ggml_time_us();
        auto batch = llama_batch_get_one(tokens.data(), count);
        if (llama_decode(ctx.get(), batch) != 0) throw std::runtime_error("prompt decode failed");
        llama_synchronize(ctx.get());
        const llama_token first = llama_sampler_sample(sampler.get(), ctx.get(), -1);
        const double wall_ms = (ggml_time_us() - started) / 1000.0;
        const auto perf = llama_perf_context(ctx.get());
        const auto sampling = llama_perf_sampler(sampler.get());
        if (first < 0 || sampling.n_sample != 1 || (count > 1 && perf.n_p_eval != count))
            throw std::runtime_error("token count verification failed");
        // For count==1 the engine classifies prompt evaluation as t_eval_ms.
        const double prefill_ms = perf.t_p_eval_ms + perf.t_eval_ms;
        const double ttft_ms = prefill_ms + sampling.t_sample_ms;
        llama_perf_context_print(ctx.get());
        llama_perf_sampler_print(sampler.get());
        // Full-precision snapshot of those same engine timing counters.
        std::cerr << "PP8_TTFT " << std::setprecision(15)
                  << "{\"ttft_ms\":" << ttft_ms
                  << ",\"prefill_ms\":" << prefill_ms
                  << ",\"first_token_ms\":" << sampling.t_sample_ms
                  << ",\"prompt_eval_ms\":" << perf.t_p_eval_ms
                  << ",\"eval_ms\":" << perf.t_eval_ms
                  << ",\"first_token_wall_ms\":" << wall_ms
                  << ",\"first_token_id\":" << first
                  << ",\"evaluated_tokens\":" << count
                  << ",\"sampled_tokens\":" << sampling.n_sample << "}\n";
        sampler.reset(); ctx.reset(); model.reset();
        llama_backend_free();
        return 0;
    } catch (const std::exception & e) {
        std::cerr << "TTFT error: " << e.what() << '\n';
        return 1;
    }
}
