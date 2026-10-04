from enum import StrEnum


class DifficultyLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class QuestionType(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    FILL_BLANK = "fill_blank"
    OUTPUT_PREDICTION = "output_prediction"
    CODE_ERROR_FIND = "code_error_find"
    CODE_FILL = "code_fill"
    OX = "ox"
    DESCRIPTIVE = "descriptive"


class FeatureTemplateSection(StrEnum):
    OVERVIEW = "overview"
    REQUIREMENTS = "requirements"
    FLOW = "flow"
    API_SPEC = "api_spec"
    CODE_VIEW = "code_view"
    BASIC_QUESTIONS = "basic_questions"
    MISSIONS = "missions"
    INTERVIEW = "interview"
    NEXT_RECOMMENDATIONS = "next_recommendations"


class TemplateType(StrEnum):
    FEATURE = "feature"
    GRAMMAR = "grammar"


class IntentType(StrEnum):
    FEATURE_TEMPLATE_GENERATE = "feature_template_generate"
    FEATURE_TEMPLATE_REGENERATE_SECTION = "feature_template_regenerate_section"
    GRAMMAR_GENERATE_QUESTIONS = "grammar_generate_questions"
    GRAMMAR_GRADE_ANSWER = "grammar_grade_answer"
    GRAMMAR_EXPLAIN_WRONG_ANSWER = "grammar_explain_wrong_answer"
    QUIZ_GRADE = "quiz_grade"
    MISSION_FEEDBACK = "mission_feedback"
    INTERVIEW_FEEDBACK = "interview_feedback"
    CODE_ANALYZE = "code_analyze"
    RECOMMEND_NEXT_TEMPLATE = "recommend_next_template"
    CHAT = "chat"
