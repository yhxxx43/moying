`timescale 1ns / 1ps
// =====================================================================
// tb_bg_subtract —— bg_subtract_ip 仿真验证
// 用最小尺寸 W=8,H=4（一帧 8 个 32bit 字），快速验证逻辑正确性。
// 背景全 0，数据 = 0xC8641E00（字节 200/100/30/0），thresh=25
//   期望输出 = 0xFFFF_FF00（>25 的字节→255，字节0→0）
// 场景：一次送整帧 + 分块送，都验证。
// =====================================================================
module tb_bg_subtract;

    localparam W = 8;
    localparam H = 4;
    localparam WORDS = W*H/4;   // 8

    reg clk = 0;
    reg rstn = 0;
    reg [31:0] s_axis_tdata = 0;
    reg s_axis_tvalid = 0;
    wire s_axis_tready;
    reg s_axis_tlast = 0;
    reg [3:0] s_axis_tkeep = 4'hF;
    wire [31:0] m_axis_tdata;
    wire m_axis_tvalid;
    reg m_axis_tready = 1;
    wire m_axis_tlast;
    reg [15:0] ctrl = 0;

    bg_subtract_ip #(.W(W), .H(H)) dut (
        .clk(clk), .rstn(rstn),
        .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready), .s_axis_tlast(s_axis_tlast),
        .s_axis_tkeep(s_axis_tkeep),
        .m_axis_tdata(m_axis_tdata), .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready), .m_axis_tlast(m_axis_tlast),
        .m_axis_tkeep(m_axis_tkeep),
        .ctrl(ctrl)
    );

    always #5 clk = ~clk;   // 100MHz

    // 输出采集
    reg [31:0] cap [0:31];
    integer cap_cnt = 0;
    always @(posedge clk) begin
        if (m_axis_tvalid && m_axis_tready) begin
            cap[cap_cnt] <= m_axis_tdata;
            cap_cnt <= cap_cnt + 1;
        end
    end

    // 期望值（固定：背景0，数据0xC8641E00，thresh25 -> 0xFFFF_FF00）
    reg [31:0] expected = 32'hFFFF_FF00;

    integer err = 0;
    integer i;

    // 送一帧（连续 valid）
    task send_frame(input [31:0] val, input last);
        integer k;
        begin
            for (k = 0; k < WORDS; k = k + 1) begin
                @(posedge clk);
                s_axis_tdata = val;
                s_axis_tvalid = 1;
                s_axis_tlast = (k == WORDS-1) ? last : 1'b0;
            end
            @(posedge clk);
            s_axis_tvalid = 0;
            s_axis_tlast = 0;
        end
    endtask

    // 送分块（每块 CHUNK 个字，块间 valid 拉低 GAP 拍）
    task send_chunked(input [31:0] val, input [3:0] chunk, input [3:0] gap);
        integer k, total;
        begin
            total = 0;
            while (total < WORDS) begin
                for (k = 0; k < chunk && total < WORDS; k = k + 1) begin
                    @(posedge clk);
                    s_axis_tdata = val;
                    s_axis_tvalid = 1;
                    s_axis_tlast = (total == WORDS-1) ? 1'b1 : 1'b0;
                    total = total + 1;
                end
                // 块间 gap
                @(posedge clk);
                s_axis_tvalid = 0;
                s_axis_tlast = 0;
                repeat(gap) @(posedge clk);
            end
        end
    endtask

    task check_output;
        integer k;
        begin
            repeat(4) @(posedge clk);   // 等输出排空
            if (cap_cnt != WORDS) begin
                $display("FAIL: 输出字数量错误, 期望 %0d, 实际 %0d", WORDS, cap_cnt);
                err = err + 1;
            end else begin
                for (k = 0; k < WORDS; k = k + 1) begin
                    if (cap[k] !== expected) begin
                        $display("FAIL: word[%0d] 期望 %h 实际 %h", k, expected, cap[k]);
                        err = err + 1;
                    end
                end
            end
        end
    endtask

    initial begin
        // 复位
        rstn = 0;
        repeat(10) @(posedge clk);
        rstn = 1;
        repeat(2) @(posedge clk);

        // ===== Test 1: 背景整帧 + 数据整帧 =====
        $display("=== Test 1: 整帧加载背景 + 整帧数据 ===");
        ctrl = #1 16'h0002;      // ctrl[1] 上升沿 -> 背景加载
        repeat(2) @(posedge clk);
        send_frame(32'h00000000, 1'b1);   // 背景全 0
        $display("  背景加载完成, bg_load=%b", dut.bg_load);

        ctrl = #1 {8'd25, 8'h00}; // ctrl[1]=0, thresh=25
        repeat(2) @(posedge clk);
        cap_cnt = 0;
        send_frame(32'hC8641E00, 1'b1);   // 数据
        check_output;
        $display("  Test1 完成, cap_cnt=%0d", cap_cnt);

        // ===== Test 2: 分块加载背景 + 分块数据 =====
        $display("=== Test 2: 分块(4+4)加载背景 + 分块数据 ===");
        cap_cnt = 0;
        ctrl = #1 16'h0002;      // 再次上升沿（先回 0 再回 1）
        ctrl = #1 16'h0000;      // 确保回 0
        repeat(2) @(posedge clk);
        ctrl = #1 16'h0002;
        repeat(2) @(posedge clk);
        send_chunked(32'h00000000, 4, 2);   // 背景分 4+4
        $display("  分块背景加载完成, bg_load=%b", dut.bg_load);

        ctrl = #1 {8'd25, 8'h00};
        repeat(2) @(posedge clk);
        send_chunked(32'hC8641E00, 4, 2);   // 数据分 4+4
        check_output;
        $display("  Test2 完成, cap_cnt=%0d", cap_cnt);

        // ===== 总结 =====
        if (err == 0) $display("\n===== 全部 PASS =====");
        else          $display("\n===== FAIL: %0d 处错误 =====", err);
        $finish;
    end

    // 关键信号波形打印
    initial begin
        $monitor("t=%0t addr=%0d bg_load=%b ctrl=%h c1d=%b c1r=%b s_valid=%b s_ready=%b m_valid=%b m_last=%b m_data=%h",
                 $time, dut.addr, dut.bg_load, ctrl, dut.ctrl1_d, dut.ctrl1_rise,
                 s_axis_tvalid, s_axis_tready, m_axis_tvalid, m_axis_tlast, m_axis_tdata);
    end

endmodule
