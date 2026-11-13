#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'

details = ARGV.delete('--details')
ignore_leaders = ARGV.delete('--ignore-leaders')
abort "usage: #{$PROGRAM_NAME} [--details] [--ignore-leaders] NDL_JSON PADDLE_JSON" unless ARGV.length == 2

ndl = JSON.parse(File.read(ARGV[0])).fetch('contents').flatten
paddle_data = JSON.parse(File.read(ARGV[1]))
paddle = paddle_data.fetch('rec_texts').each_with_index.map do |text, index|
  { 'text' => text, 'box' => paddle_data.fetch('rec_boxes').fetch(index) }
end.reject { |line| line.fetch('text').empty? }
ndl_xml = ARGV[0].sub(/\.json\z/, '.xml')
blocks = if File.file?(ndl_xml)
           File.read(ndl_xml).scan(/<BLOCK\b([^>]+)\/>/).map do |raw|
             attributes = raw.first.scan(/([A-Z]+)="([^"]*)"/).to_h
             x = attributes.fetch('X').to_i
             y = attributes.fetch('Y').to_i
             { 'type' => attributes.fetch('TYPE'), 'box' => [x, y,
               x + attributes.fetch('WIDTH').to_i, y + attributes.fetch('HEIGHT').to_i] }
           end
         else
           []
         end

def ndl_box(line)
  points = line.fetch('boundingBox')
  xs = points.map(&:first)
  ys = points.map(&:last)
  [xs.min, ys.min, xs.max, ys.max]
end

def same_column?(a, b)
  x_overlap = [a[2], b[2]].min - [a[0], b[0]].max
  y_overlap = [a[3], b[3]].min - [a[1], b[1]].max
  x_overlap >= 0.5 * [a[2] - a[0], b[2] - b[0]].min &&
    y_overlap >= 0.2 * [a[3] - a[1], b[3] - b[1]].min
end

def region_overlap(a, b)
  width = [[a[2], b[2]].min - [a[0], b[0]].max, 0].max
  height = [[a[3], b[3]].min - [a[1], b[1]].max, 0].max
  areas = [a, b].map { |box| (box[2] - box[0]) * (box[3] - box[1]) }
  width * height.to_f / areas.min
end

def align_text(left, right)
  a = left.scan(/\X/)
  b = right.scan(/\X/)
  matrix = Array.new(a.length + 1) { Array.new(b.length + 1, 0) }
  (0..a.length).each { |i| matrix[i][0] = i }
  (0..b.length).each { |j| matrix[0][j] = j }
  a.each_with_index do |char, ai|
    b.each_with_index do |other, bi|
      i = ai + 1
      j = bi + 1
      matrix[i][j] = [matrix[i - 1][j] + 1, matrix[i][j - 1] + 1,
                      matrix[i - 1][j - 1] + (char == other ? 0 : 1)].min
    end
  end

  operations = []
  i = a.length
  j = b.length
  until i.zero? && j.zero?
    if i.positive? && j.positive? &&
       matrix[i][j] == matrix[i - 1][j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1)
      operations << [a[i - 1] == b[j - 1] ? :same : :replace, a[i - 1], b[j - 1]]
      i -= 1
      j -= 1
    elsif i.positive? && matrix[i][j] == matrix[i - 1][j] + 1
      operations << [:delete, a[i - 1], nil]
      i -= 1
    else
      operations << [:insert, nil, b[j - 1]]
      j -= 1
    end
  end
  [matrix[a.length][b.length], operations.reverse]
end

def comparison_text(text, ignore_leaders)
  ignore_leaders ? text.gsub(/(?:…{2,}|\.{3,}|⋯+)/, '') : text
end

def change_spans(operations)
  spans = []
  offset = 0
  current = nil
  operations.each do |kind, old_char, new_char|
    if kind == :same
      spans << current if current
      current = nil
    else
      current ||= { offset: offset, old: String.new, new: String.new }
      current[:old] << old_char if old_char
      current[:new] << new_char if new_char
    end
    offset += 1 if old_char
  end
  spans << current if current
  spans
end

folios = blocks.select { |block| block.fetch('type') == 'ノンブル' }
excluded_folios, paddle = paddle.partition do |line|
  folios.any? { |block| region_overlap(block.fetch('box'), line.fetch('box')) >= 0.5 }
end

matched = Hash.new { |hash, key| hash[key] = [] }
unmatched = []

paddle.each do |line|
  choices = ndl.each_with_index.filter_map do |candidate, index|
    next unless same_column?(ndl_box(candidate), line.fetch('box'))

    ndl_center = (ndl_box(candidate)[0] + ndl_box(candidate)[2]) / 2.0
    paddle_center = (line.fetch('box')[0] + line.fetch('box')[2]) / 2.0
    [index, (ndl_center - paddle_center).abs]
  end
  if choices.empty?
    unmatched << line
  else
    matched[choices.min_by(&:last).first] << line
  end
end

total_distance = 0
total_chars = 0
puts "NDL lines: #{ndl.length}, Paddle lines: #{paddle.length}"
ndl.each_with_index do |line, index|
  parts = matched[index].sort_by { |part| part.fetch('box')[1] }
  if parts.empty?
    puts "unmatched NDL line #{index + 1}: x=#{ndl_box(line)[0]}"
    next
  end

  ndl_text = comparison_text(line.fetch('text'), ignore_leaders)
  paddle_text = comparison_text(parts.map { |part| part.fetch('text') }.join, ignore_leaders)
  distance, operations = align_text(ndl_text, paddle_text)
  total_distance += distance
  total_chars += ndl_text.scan(/\X/).length
  puts "line #{index + 1}: x=#{ndl_box(line)[0]} edits=#{distance} ndl_chars=#{ndl_text.scan(/\X/).length} paddle_chars=#{paddle_text.scan(/\X/).length}"
  if details
    change_spans(operations).each do |span|
      puts "  char #{span[:offset] + 1}: #{span[:old].to_json} -> #{span[:new].to_json}"
    end
  end
end

unmatched.each do |line|
  block = blocks.max_by { |candidate| region_overlap(candidate.fetch('box'), line.fetch('box')) }
  region = block && region_overlap(block.fetch('box'), line.fetch('box')) >= 0.5 ? block.fetch('type') : '未分類'
  puts "unmatched Paddle line: region=#{region} x=#{line.fetch('box')[0]} y=#{line.fetch('box')[1]} text=#{line.fetch('text')}"
end
excluded_folios.each do |line|
  puts "excluded folio: x=#{line.fetch('box')[0]} y=#{line.fetch('box')[1]} text=#{line.fetch('text')}"
end
puts "total edit distance: #{total_distance} / #{total_chars} NDL characters"
puts 'Repeated dot leaders were ignored; ・ and ー remain significant.' if ignore_leaders
puts 'This measures OCR disagreement, not accuracy.'
